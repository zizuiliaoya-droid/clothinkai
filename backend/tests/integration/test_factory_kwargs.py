"""测试工厂遇到不认识的 kwarg 必须直接 TypeError（流程线设计 9.2 第 1 行）。

工厂的 kwarg 清单是手写的；以前多传的 kwarg 被静默忽略，新列漏补进工厂时，
「两边都是 NULL」的断言会假通过。这里对每个工厂各测一条反例（拼错的 kwarg
必须报错、且报错里点出工厂名和那个 kwarg）和一条正例（清单里的 kwarg 照常生效）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio

BAD = "shipstatus"

# 调用某个工厂：ctx 里放各工厂 fixture 与预先建好的 style / blogger。
Call = Callable[[dict[str, Any], dict[str, Any]], Awaitable[Any]]


async def _user(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["factory"].user(ctx["tenant_a"], **kw)


async def _brand(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["product_factory"].brand(**kw)


async def _style(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["product_factory"].style(**kw)


async def _sku(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["product_factory"].sku(ctx["style"], **kw)


async def _blogger(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["blogger_factory"].blogger(**kw)


async def _promotion(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["promotion_factory"].promotion(
        style=ctx["style"], blogger=ctx["blogger"], **kw
    )


async def _attachment(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["attachment_factory"].attachment(**kw)


async def _settlement(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["settlement_factory"].settlement(
        style=ctx["style"], blogger=ctx["blogger"], **kw
    )


async def _batch(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["import_batch_factory"].batch(**kw)


async def _negotiation(ctx: dict[str, Any], kw: dict[str, Any]) -> Any:
    return await ctx["negotiation_factory"].negotiation(
        style=ctx["style"], blogger=ctx["blogger"], pr=ctx["pr"], **kw
    )


# (报错里的工厂名, 调用, 正例 kwarg：建出来的对象上同名属性应等于传入值)
CASES: list[tuple[str, Call, dict[str, Any]]] = [
    ("factory.user", _user, {"display_name": "张三"}),
    ("product_factory.brand", _brand, {"brand_name": "品牌甲"}),
    ("product_factory.style", _style, {"style_name": "款式甲"}),
    ("product_factory.sku", _sku, {"color": "蓝"}),
    ("blogger_factory.blogger", _blogger, {"nickname": "博主甲"}),
    ("promotion_factory.promotion", _promotion, {"platform": "抖音"}),
    ("attachment_factory.attachment", _attachment, {"filename": "a.png"}),
    ("settlement_factory.settlement", _settlement, {"note_title": "标题甲"}),
    ("import_batch_factory.batch", _batch, {"original_filename": "b.csv"}),
    ("negotiation_factory.negotiation", _negotiation, {"remark": "备注甲"}),
]
IDS = [c[0] for c in CASES]


@pytest_asyncio.fixture
async def ctx(
    factory: Any,
    tenant_a: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    attachment_factory: Any,
    settlement_factory: Any,
    import_batch_factory: Any,
    negotiation_factory: Any,
) -> dict[str, Any]:
    return {
        "negotiation_factory": negotiation_factory,
        "pr": await factory.user(tenant_a),
        "factory": factory,
        "tenant_a": tenant_a,
        "product_factory": product_factory,
        "blogger_factory": blogger_factory,
        "promotion_factory": promotion_factory,
        "attachment_factory": attachment_factory,
        "settlement_factory": settlement_factory,
        "import_batch_factory": import_batch_factory,
        "style": await product_factory.style(),
        "blogger": await blogger_factory.blogger(),
    }


@pytest.mark.parametrize(("name", "call", "good"), CASES, ids=IDS)
async def test_unknown_kwarg_raises(
    ctx: dict[str, Any], name: str, call: Call, good: dict[str, Any]
) -> None:
    with pytest.raises(TypeError) as ei:
        await call(ctx, {**good, BAD: "x"})
    msg = str(ei.value)
    assert name in msg
    assert BAD in msg
    # 认识的 kwarg 不应出现在「不认识」的清单里
    for k in good:
        assert k not in msg


@pytest.mark.parametrize(("name", "call", "good"), CASES, ids=IDS)
async def test_known_kwarg_applies(
    ctx: dict[str, Any], name: str, call: Call, good: dict[str, Any]
) -> None:
    obj = await call(ctx, good)
    for k, v in good.items():
        assert getattr(obj, k) == v, f"{name}.{k}"


async def test_special_kwargs_accepted(ctx: dict[str, Any], session: Any) -> None:
    """不是列的特殊 kwarg：user 的 roles、promotion 的 brand_comment、settlement 的 promotion_id。"""
    from sqlalchemy import select

    from app.modules.auth.models import Role, UserRole

    role = (await session.execute(select(Role).where(Role.code == "admin"))).scalar_one()
    user = await ctx["factory"].user(ctx["tenant_a"], roles=[role])
    bound = (
        (await session.execute(select(UserRole.role_id).where(UserRole.user_id == user.id)))
        .scalars()
        .all()
    )
    assert list(bound) == [role.id]

    p = await _promotion(ctx, {"brand_comment": True})
    assert p.brand_comment_attachment_id is not None

    s = await _settlement(ctx, {"promotion_id": p.id})
    assert s.promotion_id == p.id


async def test_shipping_kwargs_applied(ctx: dict[str, Any], session: Any) -> None:
    """流程线 PR-2 的收件 / 发货列与 ``items``：都给非默认值，漏补进工厂就会是 None。"""
    from datetime import UTC, datetime

    from sqlalchemy import select

    from app.modules.promotion.models import PromotionItem

    style, sku = ctx["style"], await ctx["product_factory"].sku(ctx["style"])
    pusher = await ctx["factory"].user(ctx["tenant_a"])
    at = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    cols = {
        "ship_status": "已发货",
        "receiver_name": "张三",
        "receiver_phone": "13800000000",
        "receiver_address": "上海市某区某路 1 号",
        "ship_courier": "顺丰",
        "ship_waybill": "SF123",
        "shipped_at": at,
        "ship_pushed_at": at,
        "ship_pushed_by": pusher.id,
    }
    p = await _promotion(ctx, {**cols, "items": [(style, sku)]})
    for k, v in cols.items():
        assert getattr(p, k) == v, k
    rows = (
        await session.execute(
            select(PromotionItem.style_id, PromotionItem.sku_id, PromotionItem.sort_order).where(
                PromotionItem.promotion_id == p.id
            )
        )
    ).all()
    assert [tuple(r) for r in rows] == [(style.id, sku.id, 0)]


def _negotiation_known(ctx: dict[str, Any]) -> frozenset[str]:
    known: frozenset[str] = ctx["negotiation_factory"]._KNOWN_KWARGS["negotiation"]
    return known


async def test_negotiation_known_kwargs_match_columns(ctx: dict[str, Any]) -> None:
    """谈款工厂的白名单 = 表的列（去掉参数给的三列）+ ``items``：加列漏补进工厂就红。"""
    from app.modules.negotiation.models import Negotiation
    from tests.conftest import _NEGOTIATION_ARG_COLUMNS

    known = _negotiation_known(ctx)
    columns = {c.name for c in Negotiation.__table__.columns} - _NEGOTIATION_ARG_COLUMNS
    assert "items" in known
    assert known - {"items"} == columns


async def test_negotiation_kwargs_applied(ctx: dict[str, Any], session: Any) -> None:
    """谈款工厂：每一列都给非默认值，漏补就会是默认值 / None；``items`` 按顺序插、sku 可空。"""
    from datetime import UTC, date, datetime
    from decimal import Decimal
    from uuid import uuid4

    from sqlalchemy import select

    from app.modules.negotiation.models import Negotiation, NegotiationItem
    from app.modules.product.goods_models import GoodsMain

    pf, users = ctx["product_factory"], ctx["factory"]
    style, style2 = ctx["style"], await pf.style()
    sku = await pf.sku(style)
    goods = GoodsMain(
        tenant_id=ctx["tenant_a"].id, goods_code=f"G{uuid4().hex[:8]}", goods_title="商品甲"
    )
    session.add(goods)
    await session.flush()
    manager = await users.user(ctx["tenant_a"])
    boss = await users.user(ctx["tenant_a"])
    other_pr = await users.user(ctx["tenant_a"])
    source = await _negotiation(ctx, {"status": "审核驳回"})
    at = datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
    cols: dict[str, Any] = {
        "id": uuid4(),
        "created_at": at,
        "updated_at": at,
        "goods_main_id": goods.id,
        "pr_id": other_pr.id,
        "promotion_id": None,
        "line_id": uuid4(),
        "copied_from_id": source.id,
        "cooperation_mode": "送拍",
        "platform": "抖音",
        "scheduled_publish_date": date(2026, 6, 20),
        "quote_amount": Decimal("888.00"),
        "remark": "备注乙",
        "status": "待补收货地址",
        "submitted_at": at,
        "reviewed_by": manager.id,
        "reviewed_at": at,
        "review_opinion": "主管意见",
        "final_reviewed_by": boss.id,
        "final_reviewed_at": at,
        "final_review_opinion": "老板意见",
        "receiver_name": "张三",
        "receiver_phone": "13800000000",
        "receiver_address": "上海市某区某路 1 号",
        "receiver_confirmed_at": at,
        "receiver_confirmed_by": manager.id,
    }
    # 用例本身也要覆盖全部列，否则新列漏测
    assert set(cols) == _negotiation_known(ctx) - {"items"}
    n = await _negotiation(ctx, {**cols, "items": [(style, sku), (style2, None)]})
    # expire_all 之后再读 ORM 属性会 MissingGreenlet：要比的 id 先取出来
    nid, style_id, style2_id, sku_id = n.id, style.id, style2.id, sku.id
    session.expire_all()
    fresh = (await session.execute(select(Negotiation).where(Negotiation.id == nid))).scalar_one()
    for k, v in cols.items():
        assert getattr(fresh, k) == v, k
    rows = (
        await session.execute(
            select(NegotiationItem.style_id, NegotiationItem.sku_id, NegotiationItem.sort_order)
            .where(NegotiationItem.negotiation_id == nid)
            .order_by(NegotiationItem.sort_order)
        )
    ).all()
    assert [tuple(r) for r in rows] == [(style_id, sku_id, 0), (style2_id, None, 1)]


async def test_negotiation_defaults_and_finalized(ctx: dict[str, Any]) -> None:
    """不传时：草稿、寄拍、库发 line_id；已定稿带推广单、推广单继承 line_id；不给 PR 直接报错。"""
    from uuid import uuid4

    draft = await _negotiation(ctx, {})
    assert (draft.status, draft.cooperation_mode, draft.pr_id) == ("草稿", "寄拍", ctx["pr"].id)
    assert draft.line_id is not None

    p = await _promotion(ctx, {})
    assert p.line_id is not None  # 不传 line_id 由库发号
    shared = p.line_id
    finalized = await _negotiation(
        ctx, {"status": "已定稿", "promotion_id": p.id, "line_id": shared}
    )
    assert finalized.line_id == shared

    line = uuid4()
    p2 = await _promotion(ctx, {"line_id": line})
    assert p2.line_id == line

    with pytest.raises(TypeError, match="pr 或 pr_id"):
        await ctx["negotiation_factory"].negotiation(style=ctx["style"], blogger=ctx["blogger"])
