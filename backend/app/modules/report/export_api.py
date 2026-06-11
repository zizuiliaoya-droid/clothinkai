"""U17 报表导出 API（/api/reports/{report_type}/export）。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Path, Query
from fastapi.responses import StreamingResponse

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.report.deps import ExportServiceDep
from app.modules.report.domain import resolve_time_range

router = APIRouter(prefix="/api/reports", tags=["report"])

_PresetQ = Annotated[str, Query(description="last_7d/last_30d/this_month/last_month/custom")]
_FromQ = Annotated[date | None, Query()]
_ToQ = Annotated[date | None, Query()]


@router.get(
    "/{report_type}/export",
    dependencies=[require_permission("report.export", "read")],
)
async def export_report(
    report_type: Annotated[str, Path()],
    user: CurrentActiveUser,
    service: ExportServiceDep,
    preset: _PresetQ = "last_30d",
    date_from: _FromQ = None,
    date_to: _ToQ = None,
) -> StreamingResponse:
    tr = resolve_time_range(preset, date_from, date_to)
    return await service.export(user.tenant_id, report_type, tr)


__all__ = ["router"]
