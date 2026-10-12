"""主线时间线 API（/api/lines，流程线设计 7.6）。

路由只要求登录，授权全在 service（6.5 的三层过滤；与金额时间线「粗闸门 + service 字段级判定」同一思路）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.modules.auth.deps import CurrentActiveUser, SessionDep
from app.modules.flow.schemas import LineTimelineResponse
from app.modules.flow.timeline import LineEventService

router = APIRouter(prefix="/api/lines", tags=["flow"])


@router.get("/{line_id}/events", response_model=LineTimelineResponse)
async def get_line_events(
    line_id: UUID, session: SessionDep, user: CurrentActiveUser
) -> LineTimelineResponse:
    """一条主线的时间线：最新在上、最多 500 条（超了 ``truncated``）；看不到这条线的任何单据 → 404 ``LINE_NOT_FOUND``。"""
    return await LineEventService(session).read(line_id, user)


__all__ = ["router"]
