"""商品资料导入的商品层：写哪个商品、顺手建单品商品（8a-4，设计 §5.3，J7）。

- ``load_goods_context``：按款式取两组数据（都走索引）——``code_goods`` = 编码 = 款号的商品
  （**含已软删**，唯一索引保证至多一条）；``memberships`` = 未删除、且以本款为启用成员的商品
- ``decide_goods_target``：纯函数，按六行顺序判断、命中即停，**先找可写目标、最后才看编码占用**；
  「单品」= ``is_suit`` 为假
- ``create_single_goods``：新建单品商品（编码 = 款号），审计 ``goods.create``

保证：套装永远不会被导入修改（只在单品里找目标、只新建单品）；任何已有商品编码都不会被修改
（这里没有任何改已有商品 ``goods_code`` 的语句，新建时编码 = 款号）。提示与键冲突文案里的商品
一律用显示名（简称，没填回落全称），不出现商品编码（FR-8）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.audit import AuditService
from app.modules.importer.compare import ValueKind, json_value
from app.modules.importer.outcome import ImportRowContext
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.goods_repository import GoodsRepository
from app.modules.product.goods_schemas import goods_display_name
from app.modules.product.models import Brand, Style

GOODS_TITLE_MAX_LEN = 512


@dataclass(frozen=True)
class GoodsCandidate:
    """决策用的商品快照（锁之前的查询结果）。"""

    id: UUID
    goods_code: str
    display_name: str
    is_suit: bool
    is_deleted: bool = False
    contains_style: bool = True  # 本款是它的启用成员
    member_count: int = 1  # 启用成员数


@dataclass(frozen=True)
class GoodsContext:
    code_goods: GoodsCandidate | None
    memberships: tuple[GoodsCandidate, ...]


class GoodsAction(StrEnum):
    WRITE = "write"  # 写这个已有单品（比较 / 补空 / 覆盖）
    NONE = "none"  # 不写、不建（只有提示）
    KEY_CONFLICT = "key_conflict"  # 对 code_goods 记键冲突
    CREATE = "create"  # 新建单品商品，编码 = 款号


@dataclass(frozen=True)
class GoodsDecision:
    action: GoodsAction
    rule: int  # 命中的是 §5.3 表的第几行
    target: GoodsCandidate | None = None  # WRITE 的目标 / KEY_CONFLICT 的对象
    warnings: tuple[str, ...] = field(default_factory=tuple)
    conflict_message: str | None = None


def _occupied_by(goods: GoodsCandidate) -> str:
    """编码 = 款号的商品为什么不可写。"""
    if goods.is_deleted:
        return "已删除的商品"
    if goods.is_suit:
        return "套装"
    return "成员不同的商品"


def _names(goods: tuple[GoodsCandidate, ...] | list[GoodsCandidate]) -> str:
    return "、".join(g.display_name for g in goods)


def decide_goods_target(
    code_goods: GoodsCandidate | None, memberships: tuple[GoodsCandidate, ...]
) -> GoodsDecision:
    """§5.3 六行，按顺序判断、命中即停。"""
    singles = [m for m in memberships if not m.is_suit]

    # 1. code_goods 未删除、是单品、启用成员恰好本款 → 写它
    if (
        code_goods is not None
        and not code_goods.is_deleted
        and not code_goods.is_suit
        and code_goods.contains_style
        and code_goods.member_count == 1
    ):
        others = [m for m in singles if m.id != code_goods.id]
        warnings: tuple[str, ...] = ()
        if others:
            warnings = (f"另有单品商品 {_names(others)} 未写入（只写编码与款号相同的那个）",)
        return GoodsDecision(GoodsAction.WRITE, 1, code_goods, warnings)

    # 2. 本款恰好属于 1 个单品商品 → 写它；编码被占用只给提示、不记冲突
    if len(singles) == 1:
        target = singles[0]
        warnings = ()
        if code_goods is not None:
            warnings = (
                f"款号对应的编码被{_occupied_by(code_goods)}占用，已写入「{target.display_name}」",
            )
        return GoodsDecision(GoodsAction.WRITE, 2, target, warnings)

    # 3. 本款属于 ≥ 2 个单品商品 → 不写
    if len(singles) >= 2:
        return GoodsDecision(
            GoodsAction.NONE, 3, None, (f"该款属于多个单品商品（{_names(singles)}），商品层未写入",)
        )

    # 4. 本款只在套装里 → 不建（A6）
    if memberships:
        return GoodsDecision(GoodsAction.NONE, 4, None, ("该款只在套装里，未建单品商品",))

    # 5. 本款不属于任何商品，而编码 = 款号被不兼容的商品占用 → 键冲突（AC 25）
    if code_goods is not None:
        return GoodsDecision(
            GoodsAction.KEY_CONFLICT,
            5,
            code_goods,
            conflict_message=(
                f"款号对应的商品编码已被{_occupied_by(code_goods)}占用，未建单品商品"
            ),
        )

    # 6. 新建单品商品，编码 = 款号
    return GoodsDecision(GoodsAction.CREATE, 6)


async def load_goods_context(
    session: AsyncSession, style_id: UUID, style_code: str, *, tenant_id: UUID
) -> GoodsContext:
    """两条走索引的查询：编码 = 款号的商品（含已软删）、以本款为启用成员的未删除商品。"""
    member = aliased(GoodsStyleItem)
    every = aliased(GoodsStyleItem)
    stmt = (
        select(
            GoodsMain.id,
            GoodsMain.goods_code,
            GoodsMain.goods_title,
            GoodsMain.short_name,
            GoodsMain.is_suit,
            func.count(every.id).filter(every.is_active.is_(True)).label("member_count"),
        )
        .join(
            member,
            and_(
                member.goods_main_id == GoodsMain.id,
                member.style_id == style_id,
                member.is_active.is_(True),
            ),
        )
        .join(every, every.goods_main_id == GoodsMain.id)
        .where(GoodsMain.tenant_id == tenant_id, GoodsMain.is_deleted.is_(False))
        .group_by(GoodsMain.id)
        .order_by(GoodsMain.goods_code)
    )
    memberships = tuple(
        GoodsCandidate(
            id=row.id,
            goods_code=row.goods_code,
            display_name=goods_display_name(row.goods_title, row.short_name),
            is_suit=row.is_suit,
            member_count=int(row.member_count),
        )
        for row in (await session.execute(stmt)).all()
    )

    code_stmt = select(
        GoodsMain.id,
        GoodsMain.goods_code,
        GoodsMain.goods_title,
        GoodsMain.short_name,
        GoodsMain.is_suit,
        GoodsMain.is_deleted,
    ).where(GoodsMain.tenant_id == tenant_id, GoodsMain.goods_code == style_code)
    row = (await session.execute(code_stmt)).one_or_none()
    code_goods: GoodsCandidate | None = None
    if row is not None:
        same = next((m for m in memberships if m.id == row.id), None)
        code_goods = GoodsCandidate(
            id=row.id,
            goods_code=row.goods_code,
            display_name=goods_display_name(row.goods_title, row.short_name),
            is_suit=row.is_suit,
            is_deleted=row.is_deleted,
            contains_style=same is not None,
            member_count=same.member_count if same is not None else 0,
        )
    return GoodsContext(code_goods=code_goods, memberships=memberships)


async def create_single_goods(
    session: AsyncSession,
    ctx: ImportRowContext,
    style: Style,
    parsed: Mapping[str, Any],
    brand: Brand | None,
) -> GoodsMain:
    """新建单品商品：编码 = 款号、全称 = 商品名称（截到 512）、简称 / 季节 / 品牌取本行（已过
    ``sanitize_optional``，合法才有值）、一个成员行（单件成本 = 该款启用 SKU 的最高成本价）。

    审计 ``goods.create``（``actor_type="worker"``、``user_id`` = 导入人，值经 ``json_value``）。
    并发两批次同时建同一个编码 → 撞 ``uq_goods_main_code`` → 本行失败，重试走第 1 行。
    """
    title = str(parsed.get("style_name") or style.style_name)[:GOODS_TITLE_MAX_LEN]
    goods = GoodsMain(
        tenant_id=ctx.tenant_id,
        goods_code=style.style_code,
        goods_title=title,
        short_name=parsed.get("goods_short_name"),
        season=parsed.get("season"),
        brand_id=brand.id if brand is not None else None,
        is_suit=False,
    )
    session.add(goods)
    await session.flush()
    repo = GoodsRepository(session)
    repo.add_item(
        GoodsStyleItem(
            tenant_id=ctx.tenant_id,
            goods_main_id=goods.id,
            style_id=style.id,
            single_goods_cost=await repo.default_cost_for_style(style.id),
            sort_order=0,
        )
    )
    await session.flush()
    await AuditService(session).log(
        action="goods.create",
        resource="goods_main",
        resource_id=goods.id,
        after={
            "goods_code": goods.goods_code,
            "goods_title": goods.goods_title,
            "short_name": json_value(ValueKind.TEXT, goods.short_name),
            "season": json_value(ValueKind.TEXT, goods.season),
            "brand_id": json_value(ValueKind.REF, goods.brand_id),
            "brand_name": brand.brand_name if brand is not None else None,
            "is_suit": False,
            "style_count": 1,
            "via": "import",
            "import_batch_id": str(ctx.batch_id) if ctx.batch_id is not None else None,
            "row_number": ctx.row_number,
        },
        user_id=ctx.actor_id,
        actor_type="worker",
    )
    return goods


__all__ = [
    "GoodsAction",
    "GoodsCandidate",
    "GoodsContext",
    "GoodsDecision",
    "create_single_goods",
    "decide_goods_target",
    "load_goods_context",
]
