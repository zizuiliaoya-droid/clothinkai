"""U14 报表进阶 API（工作进度/爆款约篇/店铺数据/投产报表，6 端点）。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.report.advanced_schemas import (
    PrWorkProgress,
    ProductionReport,
    StoreDailyManualUpdate,
    StoreDailyRow,
    TargetCreate,
    TargetWithActual,
)
from app.modules.report.deps import (
    ProductionServiceDep,
    StoreDailyServiceDep,
    TargetPlanningServiceDep,
    WorkProgressServiceDep,
)
from app.modules.report.domain import resolve_time_range

router = APIRouter(prefix="/api/reports", tags=["report"])

_MonthQ = Annotated[str, Query(pattern=r"^\d{4}-\d{2}$")]
_PresetQ = Annotated[str, Query(description="last_7d/last_30d/this_month/last_month/custom")]
_FromQ = Annotated[date | None, Query()]
_ToQ = Annotated[date | None, Query()]


# ----------------------------- 工作进度 ----------------------------- #


@router.get(
    "/work-progress",
    response_model=list[PrWorkProgress],
    dependencies=[require_permission("report.work_progress", "read")],
)
async def get_work_progress(
    user: CurrentActiveUser,
    service: WorkProgressServiceDep,
    month: _MonthQ,
) -> list[PrWorkProgress]:
    return await service.get_for_month(user.tenant_id, month)


# ----------------------------- 爆款约篇 ----------------------------- #


@router.post(
    "/targets",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("report.target", "write")],
)
async def set_target(
    payload: TargetCreate,
    user: CurrentActiveUser,
    service: TargetPlanningServiceDep,
) -> dict:
    await service.set_target(payload, user)
    return {"ok": True}


@router.get(
    "/targets",
    response_model=list[TargetWithActual],
    dependencies=[require_permission("report.target", "read")],
)
async def list_targets(
    user: CurrentActiveUser,
    service: TargetPlanningServiceDep,
    month: _MonthQ,
) -> list[TargetWithActual]:
    return await service.list_with_actuals(user.tenant_id, month)


# ----------------------------- 店铺数据 ----------------------------- #


@router.get(
    "/store-daily",
    response_model=list[StoreDailyRow],
    dependencies=[require_permission("report.store_daily", "read")],
)
async def get_store_daily(
    user: CurrentActiveUser,
    service: StoreDailyServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> list[StoreDailyRow]:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_dashboard(user.tenant_id, tr)


@router.put(
    "/store-daily/{day}",
    dependencies=[require_permission("report.store_daily", "write")],
)
async def update_store_daily(
    day: Annotated[date, Path()],
    payload: StoreDailyManualUpdate,
    user: CurrentActiveUser,
    service: StoreDailyServiceDep,
) -> dict:
    row = await service.upsert_manual(user.tenant_id, day, payload, user)
    return {
        "ok": True,
        "date": str(row.date),
        "ad_spend_total": str(row.ad_spend_total) if row.ad_spend_total is not None else None,
        "zhitongche_spend": str(row.zhitongche_spend) if row.zhitongche_spend is not None else None,
        "yinli_spend": str(row.yinli_spend) if row.yinli_spend is not None else None,
    }


# ----------------------------- 投产报表 ----------------------------- #


@router.get(
    "/production",
    response_model=ProductionReport,
    dependencies=[require_permission("report.production", "read")],
)
async def get_production(
    user: CurrentActiveUser,
    service: ProductionServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    exclude_brushing: bool = True,
) -> ProductionReport:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_report(
        user.tenant_id, tr, exclude_brushing=exclude_brushing
    )


__all__ = ["router"]
