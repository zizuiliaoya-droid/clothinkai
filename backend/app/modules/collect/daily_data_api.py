"""千牛日报 / 单品站内推广日报 列表查询 API（数据管理页展示）。

数据来源：导入入库（千牛增量导出 / 万相台导出）。
typed 字段直接返回；其余原始列存于 ``extra`` JSONB，一并返回供前端展开。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.collect.models import AdDaily, QianniuDaily

router = APIRouter(prefix="/api", tags=["collect-data"])


def _serialize(row: Any, typed: dict[str, Any]) -> dict[str, Any]:
    out = dict(typed)
    extra = getattr(row, "extra", None)
    if isinstance(extra, dict):
        out["extra"] = extra
    else:
        out["extra"] = {}
    return out


@router.get(
    "/qianniu",
    dependencies=[require_permission("report.store_daily", "read")],
)
async def list_qianniu_daily(
    session: SessionDep,
    _user: CurrentActiveUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    """千牛商品日报列表（38 列：typed + extra JSONB）。"""
    stmt = select(QianniuDaily)
    if date_from:
        stmt = stmt.where(QianniuDaily.date >= date_from)
    if date_to:
        stmt = stmt.where(QianniuDaily.date <= date_to)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    stmt = (
        stmt.order_by(QianniuDaily.date.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    items = [
        _serialize(
            r,
            {
                "id": str(r.id),
                "date": str(r.date),
                "platform_id": r.platform_id_snapshot,
                "visitors": r.visitors,
                "pay_amount": str(r.pay_amount) if r.pay_amount is not None else None,
                "pay_orders": r.pay_orders,
            },
        )
        for r in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get(
    "/ad-daily",
    dependencies=[require_permission("report.store_daily", "read")],
)
async def list_ad_daily(
    session: SessionDep,
    _user: CurrentActiveUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> dict[str, Any]:
    """单品站内推广日报列表（72 列：typed + extra JSONB）。"""
    stmt = select(AdDaily)
    if date_from:
        stmt = stmt.where(AdDaily.date >= date_from)
    if date_to:
        stmt = stmt.where(AdDaily.date <= date_to)
    total = int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    stmt = (
        stmt.order_by(AdDaily.date.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    items = [
        _serialize(
            r,
            {
                "id": str(r.id),
                "date": str(r.date),
                "platform_id": r.platform_id_snapshot,
                "cost": str(r.cost) if r.cost is not None else None,
                "impressions": r.impressions,
                "clicks": r.clicks,
                "gmv": str(r.gmv) if r.gmv is not None else None,
            },
        )
        for r in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


__all__ = ["router"]
