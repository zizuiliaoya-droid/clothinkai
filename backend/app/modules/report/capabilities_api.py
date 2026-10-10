"""报表能力端点 ``GET /api/reports/capabilities``（7e R1，报表视图设计第 4 节）。

只要求登录：返回的是「能看 / 能做什么」，不含业务数字。``/me`` 只返回角色，前端算不了
scope（同 ``/summaries/freshness`` 的 ``can_refresh``）：菜单「工作进度表」「BI看板」、
导出 / 刷新按钮、BI 渲染完整版还是个人版都看它。

「看全部 / 只看本人」与报表接口同一套判断：持 ``<prefix>:read`` = 全部，只持 ``read_own`` =
本人（``report.*:read`` 通配捞不到 ``read_own``，所以两个都要单独判）。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.deps import CurrentPerms
from app.modules.report.advanced_permissions import (
    REPORT_BI_READ,
    REPORT_BI_READ_OWN,
    REPORT_PUBLISH_PROGRESS_READ_OWN,
    REPORT_WORK_PROGRESS_READ_OWN,
)
from app.modules.report.advanced_schemas import ReportCapabilities
from app.modules.report.export_service import exportable_report_types
from app.modules.report.permissions import REPORT_PUBLISH_PROGRESS_READ

router = APIRouter(prefix="/api/reports", tags=["report"])


def _read_level(
    perms: EffectivePermissions, read: tuple[str, str], read_own: tuple[str, str]
) -> Literal["all", "own"] | None:
    if perms.has(*read):
        return "all"
    if perms.has(*read_own):
        return "own"
    return None


def build_capabilities(perms: EffectivePermissions) -> ReportCapabilities:
    level = _read_level(perms, REPORT_BI_READ, REPORT_BI_READ_OWN)
    bi: Literal["full", "personal"] | None = (
        "full" if level == "all" else "personal" if level == "own" else None
    )
    return ReportCapabilities(
        work_progress=_read_level(
            perms, ("report.work_progress", "read"), REPORT_WORK_PROGRESS_READ_OWN
        ),
        publish_progress=_read_level(
            perms, REPORT_PUBLISH_PROGRESS_READ, REPORT_PUBLISH_PROGRESS_READ_OWN
        ),
        bi=bi,
        production=perms.has("report.production", "read"),
        store_read=perms.has("report.store_daily", "read"),
        store_write=perms.has("report.store_daily", "write"),
        export=exportable_report_types(perms),
        refresh=perms.has("report.summary", "refresh"),
    )


@router.get("/capabilities", response_model=ReportCapabilities)
async def get_report_capabilities(perms: CurrentPerms) -> ReportCapabilities:
    """当前用户的报表能力（字段含义见 ``ReportCapabilities``）。"""
    return build_capabilities(perms)


__all__ = ["build_capabilities", "router"]
