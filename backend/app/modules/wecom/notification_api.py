"""U07 站内通知 REST API（限本人）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.wecom.deps import NotificationServiceDep
from app.modules.wecom.schemas import NotificationResponse, UnreadCountResponse

router = APIRouter(prefix="/api/notifications", tags=["notification"])


@router.get(
    "",
    response_model=list[NotificationResponse],
    dependencies=[require_permission("notification", "read")],
)
async def list_notifications(
    user: CurrentActiveUser,
    service: NotificationServiceDep,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[NotificationResponse]:
    rows = await service.list_for_user(
        user_id=user.id, unread_only=unread_only, limit=limit, offset=offset
    )
    return [NotificationResponse.model_validate(r) for r in rows]


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    dependencies=[require_permission("notification", "read")],
)
async def unread_count(
    user: CurrentActiveUser, service: NotificationServiceDep
) -> UnreadCountResponse:
    return UnreadCountResponse(count=await service.unread_count(user_id=user.id))


@router.post(
    "/{notification_id}/read",
    dependencies=[require_permission("notification", "read")],
)
async def mark_read(
    user: CurrentActiveUser,
    service: NotificationServiceDep,
    notification_id: Annotated[UUID, Path()],
) -> dict[str, bool]:
    ok = await service.mark_read(
        notification_id=notification_id, user_id=user.id
    )
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "通知不存在")
    return {"ok": True}


__all__ = ["router"]
