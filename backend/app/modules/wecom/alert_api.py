"""U15 企微预警配置 API（GET/PUT /api/wecom/alert-config）。"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.wecom.alert_schemas import AlertConfigResponse, AlertConfigUpdate
from app.modules.wecom.deps import AlertConfigServiceDep

router = APIRouter(prefix="/api/wecom", tags=["wecom"])


@router.get(
    "/alert-config",
    response_model=AlertConfigResponse | None,
    dependencies=[require_permission("wecom.alert_config", "read")],
)
async def get_alert_config(
    user: CurrentActiveUser,
    service: AlertConfigServiceDep,
) -> AlertConfigResponse | None:
    return await service.get_response()


@router.put(
    "/alert-config",
    dependencies=[require_permission("wecom.alert_config", "write")],
)
async def update_alert_config(
    payload: AlertConfigUpdate,
    user: CurrentActiveUser,
    service: AlertConfigServiceDep,
) -> dict:
    await service.upsert(payload, user)
    return {"ok": True}


__all__ = ["router"]
