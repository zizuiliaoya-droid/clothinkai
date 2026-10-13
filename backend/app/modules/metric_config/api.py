"""平台指标配置 API（``/api/settings/promotion-platforms``，报表视图设计 5.1）。

读 ``metric_config:read``（pr_manager、operations），写 ``metric_config:write``（pr_manager；管理员靠 ``*``）。
权限域为什么独立一级见 ``permissions.py``。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.auth.deps import CurrentActiveUser, CurrentPerms, SessionDep, require_permission
from app.modules.metric_config.permissions import SCOPE_METRIC_CONFIG
from app.modules.metric_config.schemas import PlatformSettingsResponse, PlatformSettingsUpdate
from app.modules.metric_config.service import PlatformMetricSettingService

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get(
    "/promotion-platforms",
    response_model=PlatformSettingsResponse,
    dependencies=[require_permission(SCOPE_METRIC_CONFIG, "read")],
)
async def get_promotion_platform_settings(
    session: SessionDep,
    user: CurrentActiveUser,
    perms: CurrentPerms,
) -> PlatformSettingsResponse:
    """5 个平台的爆文阈值与点赞折算系数。没配置过也回默认值，不 404。"""
    return await PlatformMetricSettingService(session).get_response(
        user.tenant_id, can_edit=perms.has(SCOPE_METRIC_CONFIG, "write")
    )


@router.put(
    "/promotion-platforms",
    response_model=PlatformSettingsResponse,
    dependencies=[require_permission(SCOPE_METRIC_CONFIG, "write")],
)
async def update_promotion_platform_settings(
    payload: PlatformSettingsUpdate,
    session: SessionDep,
    user: CurrentActiveUser,
) -> PlatformSettingsResponse:
    """收全量 5 行。撞汇总刷新锁 → 409 ``REPORT_SUMMARY_REFRESH_BUSY``，配置不落库。"""
    return await PlatformMetricSettingService(session).update_settings(payload, user, can_edit=True)


__all__ = ["router"]
