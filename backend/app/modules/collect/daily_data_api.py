"""千牛日报 / 单品站内推广日报 列表查询 API（数据管理页展示）。

数据来源：导入入库（千牛增量导出 / 万相台导出）。
typed 字段直接返回；其余原始列存于 ``extra`` JSONB，一并返回供前端展开。

两个端点结构同构（日期区间 + 商品ID 关键词 + 各 typed 数值列区间 + 排序），
共用下面的 ``_apply_*`` 组合器，避免两处各写一份筛选分支。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import Select, func, nullslast, select
from sqlalchemy.orm import InstrumentedAttribute

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.collect.models import AdDaily, QianniuDaily

router = APIRouter(prefix="/api", tags=["collect-data"])

SortDir = Annotated[str, Query(pattern="^(asc|desc)$")]

# 可排序列白名单：前端传字段名，这里映射到 ORM 列。不在白名单的值一律回落到默认列，
# 绝不把请求里的字符串拼进 SQL。
_QIANNIU_SORTABLE: dict[str, InstrumentedAttribute[Any]] = {
    "date": QianniuDaily.date,
    "platform_id": QianniuDaily.platform_id_snapshot,
    "visitors": QianniuDaily.visitors,
    "pay_amount": QianniuDaily.pay_amount,
    "pay_orders": QianniuDaily.pay_orders,
}
_AD_SORTABLE: dict[str, InstrumentedAttribute[Any]] = {
    "date": AdDaily.date,
    "platform_id": AdDaily.platform_id_snapshot,
    "cost": AdDaily.cost,
    "impressions": AdDaily.impressions,
    "clicks": AdDaily.clicks,
    "gmv": AdDaily.gmv,
}


def _serialize(row: Any, typed: dict[str, Any]) -> dict[str, Any]:
    out = dict(typed)
    extra = getattr(row, "extra", None)
    if isinstance(extra, dict):
        out["extra"] = extra
    else:
        out["extra"] = {}
    return out


def _apply_range(
    stmt: Select[Any],
    column: InstrumentedAttribute[Any],
    low: Any,
    high: Any,
) -> Select[Any]:
    """闭区间筛选；两端都可单独省略。"""
    if low is not None:
        stmt = stmt.where(column >= low)
    if high is not None:
        stmt = stmt.where(column <= high)
    return stmt


def _apply_sort(
    stmt: Select[Any],
    sortable: dict[str, InstrumentedAttribute[Any]],
    sort_by: str | None,
    sort_dir: str,
    *,
    default_key: str,
    tiebreak: InstrumentedAttribute[Any],
) -> Select[Any]:
    """按白名单列排序。

    数值列大量为 NULL，统一用 ``NULLS LAST``：按支付金额降序时用户要看的是金额最大的，
    把空值顶到最前面没有意义。再补一个稳定的 tiebreaker，避免分页时行序抖动。
    """
    column = sortable.get(sort_by or "", sortable[default_key])
    ordering = column.desc() if sort_dir == "desc" else column.asc()
    return stmt.order_by(nullslast(ordering), tiebreak.desc())


async def _count(session: SessionDep, stmt: Select[Any]) -> int:
    return int(
        (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )


@router.get(
    "/qianniu",
    dependencies=[require_permission("report.store_daily", "read")],
)
async def list_qianniu_daily(
    session: SessionDep,
    _user: CurrentActiveUser,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    platform_id: Annotated[str | None, Query(max_length=64)] = None,
    visitors_min: Annotated[int | None, Query(ge=0)] = None,
    visitors_max: Annotated[int | None, Query(ge=0)] = None,
    pay_amount_min: Annotated[Decimal | None, Query()] = None,
    pay_amount_max: Annotated[Decimal | None, Query()] = None,
    pay_orders_min: Annotated[int | None, Query(ge=0)] = None,
    pay_orders_max: Annotated[int | None, Query(ge=0)] = None,
    sort_by: Annotated[str | None, Query(max_length=32)] = None,
    sort_dir: SortDir = "desc",
) -> dict[str, Any]:
    """千牛商品日报列表（38 列：typed + extra JSONB）。"""
    stmt = select(QianniuDaily)
    stmt = _apply_range(stmt, QianniuDaily.date, date_from, date_to)
    if platform_id:
        stmt = stmt.where(QianniuDaily.platform_id_snapshot.ilike(f"%{platform_id}%"))
    stmt = _apply_range(stmt, QianniuDaily.visitors, visitors_min, visitors_max)
    stmt = _apply_range(stmt, QianniuDaily.pay_amount, pay_amount_min, pay_amount_max)
    stmt = _apply_range(stmt, QianniuDaily.pay_orders, pay_orders_min, pay_orders_max)

    total = await _count(session, stmt)
    stmt = _apply_sort(
        stmt,
        _QIANNIU_SORTABLE,
        sort_by,
        sort_dir,
        default_key="date",
        tiebreak=QianniuDaily.id,
    )
    stmt = stmt.limit(page_size).offset((page - 1) * page_size)
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
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
    platform_id: Annotated[str | None, Query(max_length=64)] = None,
    cost_min: Annotated[Decimal | None, Query()] = None,
    cost_max: Annotated[Decimal | None, Query()] = None,
    impressions_min: Annotated[int | None, Query(ge=0)] = None,
    impressions_max: Annotated[int | None, Query(ge=0)] = None,
    clicks_min: Annotated[int | None, Query(ge=0)] = None,
    clicks_max: Annotated[int | None, Query(ge=0)] = None,
    gmv_min: Annotated[Decimal | None, Query()] = None,
    gmv_max: Annotated[Decimal | None, Query()] = None,
    sort_by: Annotated[str | None, Query(max_length=32)] = None,
    sort_dir: SortDir = "desc",
) -> dict[str, Any]:
    """单品站内推广日报列表（72 列：typed + extra JSONB）。"""
    stmt = select(AdDaily)
    stmt = _apply_range(stmt, AdDaily.date, date_from, date_to)
    if platform_id:
        stmt = stmt.where(AdDaily.platform_id_snapshot.ilike(f"%{platform_id}%"))
    stmt = _apply_range(stmt, AdDaily.cost, cost_min, cost_max)
    stmt = _apply_range(stmt, AdDaily.impressions, impressions_min, impressions_max)
    stmt = _apply_range(stmt, AdDaily.clicks, clicks_min, clicks_max)
    stmt = _apply_range(stmt, AdDaily.gmv, gmv_min, gmv_max)

    total = await _count(session, stmt)
    stmt = _apply_sort(
        stmt,
        _AD_SORTABLE,
        sort_by,
        sort_dir,
        default_key="date",
        tiebreak=AdDaily.id,
    )
    stmt = stmt.limit(page_size).offset((page - 1) * page_size)
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
