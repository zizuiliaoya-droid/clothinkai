"""U08 report REST API（发文进度看板，4 GET 端点）。

全部只读；时间筛选 preset + custom（date_from/date_to）。
判权（7e R1）：路由 ``require_any_permission(publish_progress read, read_own)``；只持
``read_own`` 的 PR 只看本人（``resolve_pr_scope``，范围下推到 SQL）。发文进度不收 ``pr_id`` 参数。
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.deps import CurrentActiveUser, CurrentPerms, require_any_permission
from app.modules.auth.models import User
from app.modules.report.advanced_permissions import REPORT_PUBLISH_PROGRESS_READ_OWN
from app.modules.report.deps import PublishProgressServiceDep
from app.modules.report.domain import resolve_time_range
from app.modules.report.permissions import REPORT_PUBLISH_PROGRESS_READ
from app.modules.report.pr_scope import PrScope, resolve_pr_scope
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

_READ_ANY = require_any_permission(REPORT_PUBLISH_PROGRESS_READ, REPORT_PUBLISH_PROGRESS_READ_OWN)


def _scope(perms: EffectivePermissions, user: User) -> PrScope:
    return resolve_pr_scope(perms, user.id, None, REPORT_PUBLISH_PROGRESS_READ[0])


@router.get("/summary", response_model=ProgressSummary, dependencies=[_READ_ANY])
async def get_summary(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: PublishProgressServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> ProgressSummary:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_summary(user.tenant_id, tr, pr_scope=_scope(perms, user))


@router.get("/cards", response_model=StyleCardPage, dependencies=[_READ_ANY])
async def get_cards(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: PublishProgressServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> StyleCardPage:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_cards(
        user.tenant_id, tr, page=page, page_size=page_size, pr_scope=_scope(perms, user)
    )


@router.get(
    "/styles/{style_id}/by-pr",
    response_model=list[PrDetail],
    dependencies=[_READ_ANY],
)
async def get_detail_by_pr(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: PublishProgressServiceDep,
    style_id: Annotated[UUID, Path()],
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> list[PrDetail]:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_detail_by_pr(
        user.tenant_id, style_id, tr, pr_scope=_scope(perms, user)
    )


@router.get(
    "/styles/{style_id}/by-time",
    response_model=list[TimeSeriesPoint],
    dependencies=[_READ_ANY],
)
async def get_detail_by_time(
    user: CurrentActiveUser,
    perms: CurrentPerms,
    service: PublishProgressServiceDep,
    style_id: Annotated[UUID, Path()],
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> list[TimeSeriesPoint]:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.get_detail_by_time(
        user.tenant_id, style_id, tr, pr_scope=_scope(perms, user)
    )


__all__ = ["router"]
