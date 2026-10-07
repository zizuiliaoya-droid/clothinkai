"""季节选项（8a-3，FR-3.6、3.7，设计 §8.3，J13）。

季节归商品层：选项 = 字典 ``season`` 的启用值（按 ``sort_order, value``）在前，
再接商品上出现过、字典里没有的值（按值降序，新年份在前），去重。

商品页（``GET /api/goods/season-options``，``product.goods:read``）与投产页
（``GET /api/reports/season-options``，``report.production:read``）共用这一个函数——
投产页不再依赖 ``/api/dict-items``（主管没有 ``product:read``，下拉一直是空的，C-02）。
"""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.product.dict_models import DictItem
from app.modules.product.goods_models import GoodsMain

SEASON_DICT_TYPE = "season"


async def list_season_options(session: AsyncSession, tenant_id: UUID) -> list[str]:
    """字典启用值在前、商品上出现过的值在后，去重。"""
    dict_stmt = (
        sa.select(DictItem.value)
        .where(
            DictItem.tenant_id == tenant_id,
            DictItem.dict_type == SEASON_DICT_TYPE,
            DictItem.is_active.is_(True),
        )
        .order_by(DictItem.sort_order.asc(), DictItem.value.asc())
    )
    goods_stmt = (
        sa.select(GoodsMain.season)
        .where(
            GoodsMain.tenant_id == tenant_id,
            GoodsMain.is_deleted.is_(False),
            GoodsMain.season.is_not(None),
            sa.func.btrim(GoodsMain.season) != "",
        )
        .distinct()
        .order_by(GoodsMain.season.desc())
    )
    dict_values = (await session.execute(dict_stmt)).scalars().all()
    goods_values = (await session.execute(goods_stmt)).scalars().all()

    out: list[str] = []
    seen: set[str] = set()
    for value in [*dict_values, *goods_values]:
        if value is None or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


__all__ = ["SEASON_DICT_TYPE", "list_season_options"]
