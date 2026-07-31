"""TASK 15 BI 看板 API（/api/reports/bi + /api/reports/bi/layout）。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, Query

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.report.advanced_schemas import BiDashboard
from app.modules.report.bi_service import DEFAULT_BI_LAYOUT
from app.modules.report.deps import BiServiceDep, UserPreferenceServiceDep
from app.modules.report.domain import resolve_time_range

router = APIRouter(prefix="/api/reports", tags=["report"])

_PresetQ = Annotated[str, Query(description="last_7d/last_30d/this_month/last_month/custom")]
_FromQ = Annotated[date | None, Query()]
_ToQ = Annotated[date | None, Query()]

_BI_LAYOUT_KEY = "bi_layout"


@router.get(
    "/bi",
    response_model=BiDashboard,
    dependencies=[require_permission("report.production", "read")],
)
async def get_bi_dashboard(
    user: CurrentActiveUser,
    service: BiServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    granularity: Annotated[
        str,
        Query(pattern=r"^(day|week|month|year)$", description="趋势粒度"),
    ] = "day",
) -> BiDashboard:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_dashboard(
        user.tenant_id, tr, granularity=granularity
    )


@router.get(
    "/bi/layout",
    dependencies=[require_permission("report.production", "read")],
)
async def get_bi_layout(
    user: CurrentActiveUser,
    service: UserPreferenceServiceDep,
) -> dict:
    return await service.get_or_default(user.id, _BI_LAYOUT_KEY, DEFAULT_BI_LAYOUT)


@router.put(
    "/bi/layout",
    dependencies=[require_permission("report.production", "read")],
)
async def save_bi_layout(
    user: CurrentActiveUser,
    service: UserPreferenceServiceDep,
    layout: Annotated[dict, Body(...)],
) -> dict:
    await service.upsert(user, _BI_LAYOUT_KEY, layout)
    return {"ok": True}


__all__ = ["router"]
