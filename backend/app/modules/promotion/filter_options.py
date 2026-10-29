"""推广筛选的窄选项：只来自本租户 active 推广实际归属商品。"""
# ruff: noqa: S608 -- SQL 插值仅为本模块常量，所有请求值均绑定参数。

from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.goods_attribution import PROMO_GOODS_ID, PROMO_GOODS_LATERAL


class PromotionGoodsOption(BaseModel):
    id: UUID
    display_name: str


class PromotionFilterOptions(BaseModel):
    items: list[PromotionGoodsOption]
    total: int
    page: int
    page_size: int
    selected: PromotionGoodsOption | None
    seasons: list[str]


_GOODS = f"""
WITH available AS (
  SELECT DISTINCT g.id,
         COALESCE(NULLIF(BTRIM(g.short_name), ''), g.goods_title) AS display_name,
         g.season
  FROM promotion p
  {PROMO_GOODS_LATERAL}
  JOIN goods_main g ON g.id = {PROMO_GOODS_ID} AND g.tenant_id = p.tenant_id
  WHERE p.tenant_id = CAST(:tenant_id AS uuid) AND p.is_active = true
)
"""


async def list_filter_options(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    keyword: str | None,
    page: int,
    page_size: int,
    selected_id: UUID | None,
) -> PromotionFilterOptions:
    # selected 与季节从同一个可见集合取，不受当前搜索/分页限制。
    params: dict[str, Any] = {
        "tenant_id": tenant_id,
        "limit": page_size,
        "offset": (page - 1) * page_size,
    }
    where = "WHERE display_name ILIKE CAST(:keyword AS text)" if keyword else ""
    if keyword:
        params["keyword"] = f"%{keyword}%"
    total = int(
        (
            await session.execute(text(f"{_GOODS} SELECT COUNT(*) FROM available {where}"), params)
        ).scalar_one()
    )
    rows = (
        (
            await session.execute(
                text(f"""{_GOODS}
        SELECT id, display_name FROM available {where}
        ORDER BY display_name, id LIMIT CAST(:limit AS int) OFFSET CAST(:offset AS int)
    """),
                params,
            )
        )
        .mappings()
        .all()
    )
    selected = None
    if selected_id is not None:
        found = (
            (
                await session.execute(
                    text(
                        f"{_GOODS} SELECT id, display_name FROM available WHERE id = CAST(:selected_id AS uuid)"
                    ),
                    {"tenant_id": tenant_id, "selected_id": selected_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if found is not None:
            selected = PromotionGoodsOption(**found)
    seasons = (
        (
            await session.execute(
                text(f"""{_GOODS}
        SELECT DISTINCT season FROM available WHERE season IS NOT NULL AND BTRIM(season) <> '' ORDER BY season
    """),
                {"tenant_id": tenant_id},
            )
        )
        .scalars()
        .all()
    )
    return PromotionFilterOptions(
        items=[PromotionGoodsOption(**r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        selected=selected,
        seasons=list(seasons),
    )
