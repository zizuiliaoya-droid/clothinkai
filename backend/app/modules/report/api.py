"""U08 report REST API（发文进度看板，4 GET 端点）。

全部只读 + report.publish_progress:read；时间筛选 preset + custom（date_from/date_to）。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.report.deps import PublishProgressServiceDep
from app.modules.report.domain import resolve_time_range
from app.modules.report.schemas import (
    PrDetail,
    ProgressSummary,
    StyleCardPage,
    TimeSeriesPoint,
)

router = APIRouter(prefix="/api/reports/publish-progress", tags=["report"])

_PresetQ = Annotated[str, Query(description="last_7d/last_30d/this_month/last_month/custom")]
_FromQ = Annotated[date | None, Query()]
_ToQ = Annotated[date | None, Query()]


@router.get(
    "/summary",
    response_model=ProgressSummary,
    dependencies=[require_permission("report.publish_progress", "read")],
)
async def get_summary(
    user: CurrentActiveUser,
    service: PublishProgressServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> ProgressSummary:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_summary(user.tenant_id, tr)


@router.get(
    "/cards",
    response_model=StyleCardPage,
    dependencies=[require_permission("report.publish_progress", "read")],
)
async def get_cards(
    user: CurrentActiveUser,
    service: PublishProgressServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> StyleCardPage:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_cards(user.tenant_id, tr, page=page, page_size=page_size)


@router.get(
    "/styles/{style_id}/by-pr",
    response_model=list[PrDetail],
    dependencies=[require_permission("report.publish_progress", "read")],
)
async def get_detail_by_pr(
    user: CurrentActiveUser,
    service: PublishProgressServiceDep,
    style_id: Annotated[UUID, Path()],
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> list[PrDetail]:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_detail_by_pr(user.tenant_id, style_id, tr)


@router.get(
    "/styles/{style_id}/by-time",
    response_model=list[TimeSeriesPoint],
    dependencies=[require_permission("report.publish_progress", "read")],
)
async def get_detail_by_time(
    user: CurrentActiveUser,
    service: PublishProgressServiceDep,
    style_id: Annotated[UUID, Path()],
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> list[TimeSeriesPoint]:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_detail_by_time(user.tenant_id, style_id, tr)


__all__ = ["router"]
