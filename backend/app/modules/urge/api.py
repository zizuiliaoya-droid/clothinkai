"""催发任务 API（/api/urge）。

独立 router 而不是挂在 ``/api/promotions`` 下：``/api/promotions/urge/tasks`` 会和
已有的 ``/api/promotions/{promotion_id}`` 争路径（UUID 校验失败直接 422，不会继续
往下匹配）。

两套权限域的理由见 ``permissions.py``：``promotion.urge`` 要让 PR 的
``promotion.*:*`` 通配覆盖到，``urge_config`` 必须独立否则 PR 能自己改阈值。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.urge.enums import UrgeTaskStatus
from app.modules.urge.permissions import (
    SCOPE_URGE,
    SCOPE_URGE_CONFIG,
)
from app.modules.urge.schemas import (
    UrgeBatchRequest,
    UrgeBatchResponse,
    UrgeCloseRequest,
    UrgeConfigResponse,
    UrgeConfigUpdate,
    UrgeCreateRequest,
    UrgeDashboardResponse,
    UrgeRecordResponse,
    UrgeTaskDetailResponse,
    UrgeTaskListFilters,
    UrgeTaskListResponse,
)
from app.modules.urge.service import UrgeService

router = APIRouter(prefix="/api/urge", tags=["urge"])


def _svc(session: SessionDep) -> UrgeService:
    return UrgeService(session)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


@router.get(
    "/config",
    response_model=UrgeConfigResponse,
    dependencies=[require_permission(SCOPE_URGE_CONFIG, "read")],
)
async def get_urge_config(
    session: SessionDep,
    user: CurrentActiveUser,
) -> UrgeConfigResponse:
    """取生效阈值。没配置过也返回默认值而不是 404，前端少一个分支。"""
    return await _svc(session).get_config_response(user.tenant_id)


@router.put(
    "/config",
    response_model=UrgeConfigResponse,
    dependencies=[require_permission(SCOPE_URGE_CONFIG, "write")],
)
async def update_urge_config(
    payload: UrgeConfigUpdate,
    session: SessionDep,
    user: CurrentActiveUser,
) -> UrgeConfigResponse:
    return await _svc(session).update_config(payload, user)


# ---------------------------------------------------------------------------
# 看板
# ---------------------------------------------------------------------------


@router.get(
    "/dashboard",
    response_model=UrgeDashboardResponse,
    dependencies=[require_permission(SCOPE_URGE, "read")],
)
async def urge_dashboard(
    session: SessionDep,
    user: CurrentActiveUser,
) -> UrgeDashboardResponse:
    """主管看板：本周已催发 / 待催发 / 超时未回 / 催过头的。"""
    return await _svc(session).dashboard(user)


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------


@router.get(
    "/tasks",
    response_model=UrgeTaskListResponse,
    dependencies=[require_permission(SCOPE_URGE, "read")],
)
async def list_urge_tasks(
    session: SessionDep,
    user: CurrentActiveUser,
    task_status: Annotated[UrgeTaskStatus | None, Query(alias="status")] = None,
    pr_id: UUID | None = None,
    blogger_id: UUID | None = None,
    style_id: UUID | None = None,
    over_limit_only: bool = False,
    overdue_only: bool = False,
    keyword: Annotated[
        str | None, Query(max_length=64, description="博主昵称 / 推广单编码 / 款号")
    ] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> UrgeTaskListResponse:
    items, total = await _svc(session).list_tasks(
        UrgeTaskListFilters(
            status=task_status,
            pr_id=pr_id,
            blogger_id=blogger_id,
            style_id=style_id,
            over_limit_only=over_limit_only,
            overdue_only=overdue_only,
            keyword=keyword,
        ),
        user,
        page=page,
        page_size=page_size,
    )
    return UrgeTaskListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/tasks/{task_id}",
    response_model=UrgeTaskDetailResponse,
    dependencies=[require_permission(SCOPE_URGE, "read")],
)
async def get_urge_task(
    task_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
) -> UrgeTaskDetailResponse:
    """任务详情 + 催发时间线（倒序）。"""
    return await _svc(session).get_task_detail(task_id, user)


@router.post(
    "/tasks/{task_id}/close",
    response_model=UrgeTaskDetailResponse,
    dependencies=[require_permission(SCOPE_URGE, "write")],
)
async def close_urge_task(
    task_id: UUID,
    payload: UrgeCloseRequest,
    session: SessionDep,
    user: CurrentActiveUser,
) -> UrgeTaskDetailResponse:
    """手动关闭：已经决定召回或转取消，不再催了。关闭动作本身也进时间线。"""
    return await _svc(session).close_task(task_id, user, reason=payload.reason)


# ---------------------------------------------------------------------------
# 发起催发
# ---------------------------------------------------------------------------


@router.post(
    "/promotions/{promotion_id}/urge",
    response_model=UrgeTaskDetailResponse,
    dependencies=[require_permission(SCOPE_URGE, "write")],
)
async def urge_promotion(
    promotion_id: UUID,
    payload: UrgeCreateRequest,
    session: SessionDep,
    user: CurrentActiveUser,
) -> UrgeTaskDetailResponse:
    """手动催发单条（不带截图）。任务不存在就现建。"""
    return await _svc(session).urge_once(promotion_id, user, note=payload.note)


@router.post(
    "/promotions/{promotion_id}/urge-with-screenshot",
    response_model=UrgeTaskDetailResponse,
    dependencies=[require_permission(SCOPE_URGE, "write")],
)
async def urge_promotion_with_screenshot(
    promotion_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
    note: Annotated[str | None, Form(max_length=2000)] = None,
    screenshot: Annotated[UploadFile | None, File()] = None,
) -> UrgeTaskDetailResponse:
    """手动催发单条并附上聊天截图。

    后端代传而不是前端直传 R2 —— 与收款码、款式主图一致，避免浏览器依赖 bucket CORS。
    分成两个端点是因为 multipart 和 JSON body 不能共存于一个端点。
    """
    shot: tuple[str | None, str | None, bytes] | None = None
    if screenshot is not None:
        shot = (screenshot.filename, screenshot.content_type, await screenshot.read())
    return await _svc(session).urge_once(promotion_id, user, note=note, screenshot=shot)


@router.get(
    "/promotions/{promotion_id}/records",
    response_model=list[UrgeRecordResponse],
    dependencies=[require_permission(SCOPE_URGE, "read")],
)
async def list_promotion_urge_records(
    promotion_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
) -> list[UrgeRecordResponse]:
    """按推广单取催发时间线。没有任务返回空数组而不是 404。"""
    return await _svc(session).get_records_for_promotion(promotion_id, user)


@router.post(
    "/batch",
    response_model=UrgeBatchResponse,
    dependencies=[require_permission(SCOPE_URGE, "write")],
)
async def urge_batch(
    payload: UrgeBatchRequest,
    session: SessionDep,
    user: CurrentActiveUser,
) -> UrgeBatchResponse:
    """按款式批量催发。不支持截图 —— 一次催十几个博主贴同一张图没有留痕价值。"""
    return await _svc(session).urge_batch(payload.style_id, user, note=payload.note)


__all__ = ["router"]
