"""谈款审核仓储层。

列表与详情都要带出博主昵称、款式、商品、PR 名字 —— 走 raw SQL 一次 JOIN 完，
用 ORM 关系会变成逐行回表。写操作用 ORM（要走审计与状态流转）。
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.negotiation.models import Negotiation

# 详情与列表共用的 JOIN + 投影，避免两处口径漂移
_SELECT_COLUMNS = """
    n.id, n.blogger_id, n.style_id, n.goods_main_id, n.pr_id, n.promotion_id,
    n.cooperation_mode, n.platform, n.scheduled_publish_date, n.quote_amount,
    n.remark, n.status, n.submitted_at, n.reviewed_by, n.reviewed_at,
    n.review_opinion, n.created_at, n.updated_at,
    b.nickname AS blogger_nickname,
    s.style_code, s.style_name,
    g.goods_code, g.goods_title, COALESCE(g.is_suit, false) AS goods_is_suit,
    -- display_name 可空，回落到 username 保证界面上总有个人名
    COALESCE(pru.display_name, pru.username) AS pr_name,
    COALESCE(rvu.display_name, rvu.username) AS reviewer_name,
    p.internal_code AS promotion_internal_code
"""

_JOINS = """
    FROM negotiation n
    LEFT JOIN blogger b ON b.id = n.blogger_id
    LEFT JOIN style s ON s.id = n.style_id
    LEFT JOIN goods_main g ON g.id = n.goods_main_id
    LEFT JOIN "user" pru ON pru.id = n.pr_id
    LEFT JOIN "user" rvu ON rvu.id = n.reviewed_by
    LEFT JOIN promotion p ON p.id = n.promotion_id
"""


class NegotiationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get ----------------------- #

    async def get_by_id(self, negotiation_id: UUID) -> Negotiation | None:
        return await self._session.get(Negotiation, negotiation_id)

    async def get_detail(self, negotiation_id: UUID) -> Mapping[str, Any] | None:
        row = (
            await self._session.execute(
                text(f"SELECT {_SELECT_COLUMNS} {_JOINS} WHERE n.id = :nid"),
                {"nid": negotiation_id},
            )
        ).mappings()
        first = row.first()
        return dict(first) if first is not None else None

    # ----------------------- list ----------------------- #

    async def list_detailed(
        self,
        *,
        tenant_id: UUID,
        status: str | None = None,
        blogger_id: UUID | None = None,
        style_id: UUID | None = None,
        pr_id: UUID | None = None,
        cooperation_mode: str | None = None,
        keyword: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[Mapping[str, Any]], int]:
        clauses = ["n.tenant_id = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": tenant_id}
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
                      -- 待审核的排最前：主管打开页面就该先看到要处理的
                      CASE n.status WHEN '待审核' THEN 0 WHEN '草稿' THEN 1
                                    WHEN '审核驳回' THEN 2 ELSE 3 END,
                      n.created_at DESC
                    OFFSET :offset LIMIT :limit
                    """
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        return [dict(r) for r in rows], total

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
                    """
                    SELECT p.id AS promotion_id, p.internal_code, p.style_id,
                           p.style_code_snapshot AS style_code,
                           p.style_short_name_snapshot AS style_name,
                           s.main_image_key AS style_main_image_key,
                           p.cooperation_date, p.cooperation_mode, p.publish_status,
                           p.actual_publish_date, p.like_count, p.quote_amount,
                           p.total_promo_cost, p.metrics_recorded_at,
                           p.platform
                    FROM promotion p
                    LEFT JOIN style s ON s.id = p.style_id
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

    async def count_by_status(self, *, tenant_id: UUID) -> dict[str, int]:
        """各状态的单据数，给前端做 Tab 角标。"""
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT status, COUNT(*) AS n FROM negotiation
                    WHERE tenant_id = :tenant_id GROUP BY status
                    """
                ),
                {"tenant_id": tenant_id},
            )
        ).all()
        return {str(r[0]): int(r[1]) for r in rows}

    async def exists_pending_duplicate(
        self, *, tenant_id: UUID, blogger_id: UUID, style_id: UUID, exclude_id: UUID | None = None
    ) -> bool:
        """同博主 + 同款式是否已有在途谈款单（草稿 / 待审核）。

        不阻塞，只给前端一个提示 —— 同一个博主推同一款两次是合理的（比如补发），
        但大概率是重复录入。
        """
        stmt = select(Negotiation.id).where(
            Negotiation.tenant_id == tenant_id,
            Negotiation.blogger_id == blogger_id,
            Negotiation.style_id == style_id,
            Negotiation.status.in_(("草稿", "待审核")),
        )
        if exclude_id is not None:
            stmt = stmt.where(Negotiation.id != exclude_id)
        return (await self._session.execute(stmt.limit(1))).scalar_one_or_none() is not None


__all__ = ["NegotiationRepository"]
