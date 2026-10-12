"""主线时间线 API（/api/lines，流程线设计 7.6）。

路由只要求登录，授权全在 service（6.5 的三层过滤；与金额时间线「粗闸门 + service 字段级判定」同一思路）。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile, status

from app.modules.auth.deps import CurrentActiveUser, SessionDep
from app.modules.flow.schemas import LineTimelineResponse, TimelineEvent
from app.modules.flow.timeline import COMMENT_IMAGE_MAX_BYTES, LineEventService

router = APIRouter(prefix="/api/lines", tags=["flow"])


@router.get("/{line_id}/events", response_model=LineTimelineResponse)
async def get_line_events(
    line_id: UUID, session: SessionDep, user: CurrentActiveUser
) -> LineTimelineResponse:
    """一条主线的时间线：最新在上、最多 500 条（超了 ``truncated``）；看不到这条线的任何单据 → 404 ``LINE_NOT_FOUND``。"""
    return await LineEventService(session).read(line_id, user)


@router.post(
    "/{line_id}/comments",
    response_model=TimelineEvent,
    status_code=status.HTTP_201_CREATED,
)
async def post_line_comment(
    line_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
    text: Annotated[str | None, Form(max_length=2000)] = None,
    image: Annotated[
        UploadFile | None, File(description="一张图（JPG / PNG / WebP，≤10MB）")
    ] = None,
) -> TimelineEvent:
    """留言 / 贴图（N11 与推广阶段共用）：文字或一张图至少一项，多张图发多条；返回新写的那条事件。

    后端代传图片（与收款码、催发截图同一做法，不依赖桶的 CORS）。
    """
    shot: tuple[str | None, str | None, bytes] | None = None
    if image is not None:
        try:
            # 多读 1 字节：超限由 check_image_payload 报「不能超过 10MB」，不整个读进内存
            data = await image.read(COMMENT_IMAGE_MAX_BYTES + 1)
        finally:
            await image.close()
        shot = (image.filename, image.content_type, data)
    return await LineEventService(session).comment(line_id, user, text=text, image=shot)


__all__ = ["router"]
