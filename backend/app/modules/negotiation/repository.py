"""谈款审核仓储层。

列表与详情都要带出博主昵称、款式、商品、PR 名字 —— 走 raw SQL 一次 JOIN 完，
用 ORM 关系会变成逐行回表。写操作用 ORM（要走审计与状态流转）。
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.negotiation.models import Negotiation, NegotiationItem
from app.modules.product.models import Sku, Style
from app.modules.promotion.display_name import PROMOTION_DISPLAY_SHORT_NAME_SQL

# 详情与列表共用的 JOIN + 投影，避免两处口径漂移
_SELECT_COLUMNS = """
    n.id, n.blogger_id, n.style_id, n.goods_main_id, n.pr_id, n.promotion_id,
    n.cooperation_mode, n.platform, n.scheduled_publish_date, n.quote_amount,
    n.remark, n.status, n.submitted_at, n.reviewed_by, n.reviewed_at,
    n.review_opinion, n.created_at, n.updated_at,
    n.line_id, n.copied_from_id,
    n.final_reviewed_by, n.final_reviewed_at, n.final_review_opinion,
    n.receiver_name, n.receiver_phone, n.receiver_address,
    n.receiver_confirmed_at, n.receiver_confirmed_by,
    b.nickname AS blogger_nickname,
    s.style_code, s.style_name,
    -- 品名：商品简称 → 款式简称 → 款式名（BTRIM 后为空算没填，同 promotion/display_name.py）
    COALESCE(NULLIF(BTRIM(g.short_name), ''), NULLIF(BTRIM(s.short_name), ''), s.style_name)
      AS display_short_name,
    g.goods_code, g.goods_title, COALESCE(g.is_suit, false) AS goods_is_suit,
    -- display_name 可空，回落到 username 保证界面上总有个人名
    COALESCE(pru.display_name, pru.username) AS pr_name,
    COALESCE(rvu.display_name, rvu.username) AS reviewer_name,
    COALESCE(fvu.display_name, fvu.username) AS final_reviewer_name,
    p.internal_code AS promotion_internal_code
"""

_JOINS = """
    FROM negotiation n
    LEFT JOIN blogger b ON b.id = n.blogger_id
    LEFT JOIN style s ON s.id = n.style_id
    LEFT JOIN goods_main g ON g.id = n.goods_main_id
    LEFT JOIN "user" pru ON pru.id = n.pr_id
    LEFT JOIN "user" rvu ON rvu.id = n.reviewed_by
    LEFT JOIN "user" fvu ON fvu.id = n.final_reviewed_by
    LEFT JOIN promotion p ON p.id = n.promotion_id
"""

# 可见范围（3.2）：本人；或持 negotiation_all:read（管理员靠 *）看草稿之外的。草稿只有本人看得到
_VISIBLE = (
    "(n.pr_id = CAST(:viewer_id AS uuid) OR (n.status <> '草稿' AND CAST(:see_all AS boolean)))"
)

# 超 36 小时（7c-6）：截止时刻由 service 用 Python 算好传进来（按实际时长比，freeze_time 可测）
_OVERDUE = "(n.status = '主管待审' AND n.submitted_at < CAST(:overdue_cutoff AS timestamptz))"


@dataclass(frozen=True)
class NegotiationItemView:
    """谈款明细一行 + 款式 / SKU 上要显示的列。``sku_id`` 可空（草稿还没选）。"""

    id: UUID
    negotiation_id: UUID
    style_id: UUID
    sku_id: UUID | None
    color: str | None
    size: str | None
    sku_style_id: UUID | None
    """SKU 实际所属款式：≠ 行的款式（或 SKU 已删）都算没选齐（N3）。"""
    sku_is_deleted: bool | None
    style_name: str
    style_short_name: str | None
    style_main_image_key: str | None
    style_code: str


# 博主 hover 卡「历史合作」的投影（取自推广单）。display_short_name = 商品简称，没填回落
# 建单快照（7a-8，规则见 promotion/display_name.py）；style_name 仍是快照，接口兼容保留
_COOPERATION_COLUMNS = f"""
    p.id AS promotion_id, p.internal_code, p.style_id,
    p.style_code_snapshot AS style_code,
    p.style_short_name_snapshot AS style_name,
    {PROMOTION_DISPLAY_SHORT_NAME_SQL} AS display_short_name,
    g.goods_title AS goods_title,
    s.main_image_key AS style_main_image_key,
    p.cooperation_date, p.cooperation_mode, p.publish_status,
    p.actual_publish_date, p.like_count, p.quote_amount,
    p.total_promo_cost, p.metrics_recorded_at,
    p.platform
"""

_COOPERATION_JOINS = """
    FROM promotion p
    LEFT JOIN style s ON s.id = p.style_id
    LEFT JOIN goods_main g ON g.id = p.goods_main_id AND g.tenant_id = p.tenant_id
"""


class NegotiationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get ----------------------- #

    async def get_by_id(self, negotiation_id: UUID) -> Negotiation | None:
        return await self._session.get(Negotiation, negotiation_id)

    async def get_detail(
        self, negotiation_id: UUID, *, viewer_id: UUID, see_all: bool
    ) -> Mapping[str, Any] | None:
        """看不到（别人的草稿、没有 negotiation_all:read 时别人的单）同不存在，返回 None。"""
        row = (
            await self._session.execute(
                text(f"SELECT {_SELECT_COLUMNS} {_JOINS} WHERE n.id = :nid AND {_VISIBLE}"),
                {"nid": negotiation_id, "viewer_id": viewer_id, "see_all": see_all},
            )
        ).mappings()
        first = row.first()
        return dict(first) if first is not None else None

    # ----------------------- list ----------------------- #

    async def list_detailed(
        self,
        *,
        tenant_id: UUID,
        viewer_id: UUID,
        see_all: bool,
        overdue_cutoff: datetime,
        status: str | None = None,
        blogger_id: UUID | None = None,
        style_id: UUID | None = None,
        pr_id: UUID | None = None,
        cooperation_mode: str | None = None,
        keyword: str | None = None,
        overdue_only: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[Mapping[str, Any]], int]:
        clauses = ["n.tenant_id = :tenant_id", _VISIBLE]
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "viewer_id": viewer_id,
            "see_all": see_all,
        }
        if overdue_only:
            clauses.append(_OVERDUE)
            params["overdue_cutoff"] = overdue_cutoff
        if status:
            clauses.append("n.status = :status")
            params["status"] = status
        if blogger_id is not None:
            clauses.append("n.blogger_id = :blogger_id")
            params["blogger_id"] = blogger_id
        if style_id is not None:
            clauses.append("n.style_id = :style_id")
            params["style_id"] = style_id
        if pr_id is not None:
            clauses.append("n.pr_id = :pr_id")
            params["pr_id"] = pr_id
        if cooperation_mode:
            clauses.append("n.cooperation_mode = :cooperation_mode")
            params["cooperation_mode"] = cooperation_mode
        if keyword:
            clauses.append(
                "(b.nickname ILIKE :kw OR s.style_code ILIKE :kw OR s.style_name ILIKE :kw)"
            )
            params["kw"] = f"%{keyword}%"
        where = " AND ".join(clauses)

        total = int(
            (
                await self._session.execute(text(f"SELECT COUNT(*) {_JOINS} WHERE {where}"), params)
            ).scalar_one()
        )
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    {_JOINS}
                    WHERE {where}
                    ORDER BY
                      -- 主管待审的排最前：主管打开页面就该先看到要处理的
                      CASE n.status WHEN '主管待审' THEN 0 WHEN '草稿' THEN 1
                                    WHEN '审核驳回' THEN 2 ELSE 3 END,
                      n.created_at DESC, n.id
                    OFFSET :offset LIMIT :limit
                    """
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        return [dict(r) for r in rows], total

    async def list_items(
        self, negotiation_ids: Sequence[UUID]
    ) -> dict[UUID, builtins.list[NegotiationItemView]]:
        """一次查出这些谈款的全部明细（列表一页一条查询），按 ``sort_order`` 排。"""
        if not negotiation_ids:
            return {}
        stmt = (
            select(
                NegotiationItem.id,
                NegotiationItem.negotiation_id,
                NegotiationItem.style_id,
                NegotiationItem.sku_id,
                Sku.color,
                Sku.size,
                Sku.style_id,
                Sku.is_deleted,
                Style.style_name,
                Style.short_name,
                Style.main_image_key,
                Style.style_code,
            )
            .join(Style, Style.id == NegotiationItem.style_id)
            .outerjoin(Sku, Sku.id == NegotiationItem.sku_id)
            .where(NegotiationItem.negotiation_id.in_(list(negotiation_ids)))
            .order_by(
                NegotiationItem.negotiation_id, NegotiationItem.sort_order, NegotiationItem.id
            )
        )
        grouped: dict[UUID, builtins.list[NegotiationItemView]] = {}
        for row in (await self._session.execute(stmt)).all():
            grouped.setdefault(row[1], []).append(NegotiationItemView(*row))
        return grouped

    async def receiver_suggestion(
        self, *, tenant_id: UUID, blogger_id: UUID
    ) -> Mapping[str, Any] | None:
        """收货信息默认值（3.2）：该博主最近一张有收件信息的推广单；没有再取最近一张已定稿谈款。

        ``source`` = promotion / negotiation，``at`` 是给标签用的时刻（推广单建单、谈款确认收货）。
        """
        has_receiver = (
            "(x.receiver_name IS NOT NULL OR x.receiver_phone IS NOT NULL"
            " OR x.receiver_address IS NOT NULL)"
        )
        params = {"tenant_id": tenant_id, "blogger_id": blogger_id}
        row = (
            (
                await self._session.execute(
                    text(
                        f"""
                        SELECT 'promotion' AS source, x.internal_code, x.created_at AS at,
                               x.receiver_name, x.receiver_phone, x.receiver_address
                        FROM promotion x
                        WHERE x.tenant_id = :tenant_id AND x.blogger_id = :blogger_id
                          AND {has_receiver}
                        ORDER BY x.created_at DESC, x.id DESC
                        LIMIT 1
                        """  # noqa: S608  插值只有模块常量，用户输入走绑定参数
                    ),
                    params,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            row = (
                (
                    await self._session.execute(
                        text(
                            f"""
                            SELECT 'negotiation' AS source, NULL AS internal_code,
                                   COALESCE(x.receiver_confirmed_at, x.updated_at) AS at,
                                   x.receiver_name, x.receiver_phone, x.receiver_address
                            FROM negotiation x
                            WHERE x.tenant_id = :tenant_id AND x.blogger_id = :blogger_id
                              AND x.status = '已定稿' AND {has_receiver}
                            ORDER BY COALESCE(x.receiver_confirmed_at, x.updated_at) DESC, x.id DESC
                            LIMIT 1
                            """  # noqa: S608  插值只有模块常量，用户输入走绑定参数
                        ),
                        params,
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row is not None else None

    # ----------------------- 博主历史合作 ----------------------- #

    async def blogger_cooperations(
        self, *, tenant_id: UUID, blogger_id: UUID, limit: int = 5
    ) -> tuple[builtins.list[Mapping[str, Any]], int]:
        """某博主最近 N 次合作（取自推广单，不是谈款单）。

        用推广单而不是谈款单：hover 卡要看的是「实际推了什么、效果如何」，
        谈款单里有草稿和被驳回的，那些没真的推出去。
        """
        total = int(
            (
                await self._session.execute(
                    text(
                        """
                        SELECT COUNT(*) FROM promotion
                        WHERE tenant_id = :tenant_id AND blogger_id = :blogger_id
                          AND is_active = true
                        """
                    ),
                    {"tenant_id": tenant_id, "blogger_id": blogger_id},
                )
            ).scalar_one()
        )
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT {_COOPERATION_COLUMNS}
                    {_COOPERATION_JOINS}
                    WHERE p.tenant_id = :tenant_id AND p.blogger_id = :blogger_id
                      AND p.is_active = true
                    ORDER BY p.cooperation_date DESC, p.created_at DESC
                    LIMIT :limit
                    """
                ),
                {"tenant_id": tenant_id, "blogger_id": blogger_id, "limit": limit},
            )
        ).mappings()
        return [dict(r) for r in rows], total

    # ----------------------- write ----------------------- #

    def add(self, negotiation: Negotiation) -> None:
        self._session.add(negotiation)

    async def style_exists(self, style_id: UUID) -> bool:
        row = (
            await self._session.execute(
                text("SELECT 1 FROM style WHERE id = :sid AND is_deleted = false"),
                {"sid": style_id},
            )
        ).first()
        return row is not None

    async def goods_contains_style(self, *, goods_main_id: UUID, style_id: UUID) -> bool:
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT 1 FROM goods_style_item
                    WHERE goods_main_id = :gid AND style_id = :sid AND is_active = true
                    """
                ),
                {"gid": goods_main_id, "sid": style_id},
            )
        ).first()
        return row is not None

    async def count_by_status(
        self, *, tenant_id: UUID, viewer_id: UUID, see_all: bool, overdue_cutoff: datetime
    ) -> tuple[dict[str, int], int]:
        """各状态的单据数（与列表同一可见范围），外加主管待审超 36 小时的数，给前端做 Tab 角标。"""
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT n.status, COUNT(*) AS n,
                           COUNT(*) FILTER (WHERE {_OVERDUE}) AS overdue
                    FROM negotiation n
                    WHERE n.tenant_id = :tenant_id AND {_VISIBLE}
                    GROUP BY n.status
                    """  # noqa: S608  插值只有模块常量，用户输入走绑定参数
                ),
                {
                    "tenant_id": tenant_id,
                    "viewer_id": viewer_id,
                    "see_all": see_all,
                    "overdue_cutoff": overdue_cutoff,
                },
            )
        ).all()
        return {str(r[0]): int(r[1]) for r in rows}, sum(int(r[2]) for r in rows)

    async def exists_pending_duplicate(
        self, *, tenant_id: UUID, blogger_id: UUID, style_id: UUID, exclude_id: UUID | None = None
    ) -> bool:
        """同博主 + 同款式是否已有在途谈款单（草稿 / 主管待审）。

        不阻塞，只给前端一个提示 —— 同一个博主推同一款两次是合理的（比如补发），
        但大概率是重复录入。
        """
        stmt = select(Negotiation.id).where(
            Negotiation.tenant_id == tenant_id,
            Negotiation.blogger_id == blogger_id,
            Negotiation.style_id == style_id,
            Negotiation.status.in_(("草稿", "主管待审")),
        )
        if exclude_id is not None:
            stmt = stmt.where(Negotiation.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).scalar_one_or_none() is not None


__all__ = ["NegotiationItemView", "NegotiationRepository"]
