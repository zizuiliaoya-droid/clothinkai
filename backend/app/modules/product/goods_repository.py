"""商品（goods_main / goods_style_item）仓储层。

列表查询走 raw SQL：一次要带出品牌名、成员成本汇总、链接数三样东西，用 ORM 关系会变成
逐行回表。写操作用 ORM，因为要走审计与软删留痕。
"""

from __future__ import annotations

import builtins
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.product.goods_models import GoodsMain, GoodsStyleItem


@dataclass(frozen=True)
class GoodsListFilters:
    """商品列表筛选条件。"""

    keyword: str | None = None
    """同时搜商品编码、商品名、成员款式的货号与款名 —— 手里可能只有其中任意一个。"""

    category: str | None = None
    season: str | None = None
    brand_id: UUID | None = None
    is_suit: bool | None = None
    is_active: bool | None = None
    include_inactive: bool = False
    unlinked_only: bool = False
    """只看还没挂平台链接的商品 —— 这些商品不会出现在任何销售数据里。"""


class GoodsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get / exists ----------------------- #

    async def get_by_id(self, goods_id: UUID, *, include_deleted: bool = False) -> GoodsMain | None:
        goods = await self._session.get(GoodsMain, goods_id)
        if goods is None:
            return None
        if goods.is_deleted and not include_deleted:
            return None
        return goods

    async def get_by_code(
        self, goods_code: str, *, include_deleted: bool = False
    ) -> GoodsMain | None:
        stmt = select(GoodsMain).where(GoodsMain.goods_code == goods_code)
        if not include_deleted:
            stmt = stmt.where(GoodsMain.is_deleted.is_(False))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def code_exists(self, goods_code: str) -> bool:
        """含已软删的商品也算占用 —— goods_code 是报表归属的引用键，
        复用已删编码会让历史数据指向一个语义不同的新商品。"""
        stmt = (
            select(sa.literal(1))
            .select_from(GoodsMain)
            .where(GoodsMain.goods_code == goods_code)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    # ----------------------- list ----------------------- #

    async def list(
        self,
        *,
        tenant_id: UUID,
        filters: GoodsListFilters,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[Mapping[str, Any]], int]:
        clauses = ["g.tenant_id = :tenant_id", "g.is_deleted = false"]
        params: dict[str, Any] = {"tenant_id": tenant_id}

        # 与款式列表同样的三态：显式给 is_active 只看该状态，
        # include_inactive 看全部，都不给则只看启用。
        if filters.is_active is not None:
            clauses.append("g.is_active = :is_active")
            params["is_active"] = filters.is_active
        elif not filters.include_inactive:
            clauses.append("g.is_active = true")

        if filters.category is not None:
            clauses.append("g.category = :category")
            params["category"] = filters.category
        if filters.season is not None:
            clauses.append("g.season = :season")
            params["season"] = filters.season
        if filters.brand_id is not None:
            clauses.append("g.brand_id = :brand_id")
            params["brand_id"] = filters.brand_id
        if filters.is_suit is not None:
            clauses.append("g.is_suit = :is_suit")
            params["is_suit"] = filters.is_suit
        if filters.unlinked_only:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM platform_product pp WHERE pp.goods_main_id = g.id)"
            )
        if filters.keyword:
            clauses.append(
                """(g.goods_code ILIKE :kw OR g.goods_title ILIKE :kw
                    OR EXISTS (
                        SELECT 1 FROM goods_style_item gi
                        JOIN style s ON s.id = gi.style_id
                        WHERE gi.goods_main_id = g.id
                          AND (s.style_code ILIKE :kw OR s.style_name ILIKE :kw)
                    ))"""
            )
            params["kw"] = f"%{filters.keyword}%"

        where = " AND ".join(clauses)

        total = int(
            (
                await self._session.execute(
                    text(f"SELECT COUNT(*) FROM goods_main g WHERE {where}"), params
                )
            ).scalar_one()
        )

        # 成本与链接数用标量子查询而不是 JOIN + GROUP BY：两个聚合来自不同表，
        # 一起 JOIN 会让成员行与链接行相乘，成本被重复累加。
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT g.id, g.goods_code, g.goods_title, g.category, g.season,
                           g.brand_id, b.brand_name, g.main_image_key, g.remark,
                           g.is_suit, g.is_active, g.created_at, g.updated_at,
                           COALESCE((
                               SELECT SUM(gi.single_goods_cost)
                               FROM goods_style_item gi
                               WHERE gi.goods_main_id = g.id AND gi.is_active = true
                           ), NULL) AS total_cost,
                           (
                               SELECT COUNT(*)
                               FROM goods_style_item gi
                               WHERE gi.goods_main_id = g.id AND gi.is_active = true
                                 AND gi.single_goods_cost IS NULL
                           ) AS cost_missing_count,
                           (
                               SELECT COUNT(*)
                               FROM platform_product pp
                               WHERE pp.goods_main_id = g.id
                           ) AS link_count
                    FROM goods_main g
                    LEFT JOIN brand b ON b.id = g.brand_id
                    WHERE {where}
                    ORDER BY g.is_active DESC, g.created_at DESC
                    OFFSET :offset LIMIT :limit
                    """
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        return [dict(r) for r in rows], total

    # ----------------------- 成员款式 ----------------------- #

    async def items_by_goods_ids(
        self, goods_ids: Collection[UUID]
    ) -> dict[UUID, builtins.list[Mapping[str, Any]]]:
        """批量取成员款式，一次查完避免 N+1。"""
        ids = builtins.list(dict.fromkeys(goods_ids))
        if not ids:
            return {}
        rows = (
            await self._session.execute(
                text(
                    """
                    SELECT gi.goods_main_id, gi.id, gi.style_id, gi.single_goods_cost,
                           gi.sort_order, gi.is_active,
                           s.style_code, s.style_name
                    FROM goods_style_item gi
                    LEFT JOIN style s ON s.id = gi.style_id
                    WHERE gi.goods_main_id = ANY(:ids)
                    ORDER BY gi.sort_order, s.style_code
                    """
                ),
                {"ids": ids},
            )
        ).mappings()
        out: dict[UUID, builtins.list[Mapping[str, Any]]] = {gid: [] for gid in ids}
        for row in rows:
            out[row["goods_main_id"]].append(dict(row))
        return out

    async def list_items(self, goods_main_id: UUID) -> Sequence[GoodsStyleItem]:
        stmt = (
            select(GoodsStyleItem)
            .where(GoodsStyleItem.goods_main_id == goods_main_id)
            .order_by(GoodsStyleItem.sort_order)
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def default_cost_for_style(self, style_id: UUID) -> Decimal | None:
        """款式的参考成本：取其启用 SKU 的最高成本价。

        用最高而不是平均：套装报价按最贵的那个规格算才不会亏，而同款不同色的成本价
        在这套数据里基本一致，取最高等于取实际值。
        """
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT MAX(cost_price) AS cost
                    FROM sku
                    WHERE style_id = :style_id
                      AND is_deleted = false
                      AND is_active = true
                      AND cost_price IS NOT NULL
                    """
                ),
                {"style_id": style_id},
            )
        ).one_or_none()
        if row is None or row[0] is None:
            return None
        return Decimal(row[0])

    # ----------------------- 引用检查 ----------------------- #

    async def reference_counts(self, goods_main_id: UUID) -> dict[str, int]:
        """删除前检查下游引用。

        平台链接和推广记录都带 ``goods_main_id``，直接物理删商品会让这些行指向一个
        不存在的商品（外键是 SET NULL / RESTRICT，但报表归属会丢）。所以商品只软删，
        且仍挂着链接时拒绝删除 —— 先把链接改到别的商品上。
        """
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM platform_product WHERE goods_main_id = :gid)
                            AS platform_links,
                        (SELECT COUNT(*) FROM promotion WHERE goods_main_id = :gid)
                            AS promotions
                    """
                ),
                {"gid": goods_main_id},
            )
        ).one()
        return {"platform_links": int(row[0]), "promotions": int(row[1])}

    # ----------------------- write ----------------------- #

    def add(self, goods: GoodsMain) -> None:
        self._session.add(goods)

    def add_item(self, item: GoodsStyleItem) -> None:
        self._session.add(item)

    async def delete_items(self, goods_main_id: UUID) -> None:
        """整体替换成员前先清空旧成员行。

        物理删而不是停用：成员行没有被任何外部表引用，``single_goods_cost`` 的历史值
        也没有报表意义（报表读的是订单与推广的落库金额）。
        """
        await self._session.execute(
            sa.delete(GoodsStyleItem).where(GoodsStyleItem.goods_main_id == goods_main_id)
        )


__all__ = ["GoodsListFilters", "GoodsRepository"]
