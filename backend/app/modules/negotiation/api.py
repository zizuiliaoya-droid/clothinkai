"""谈款审核 API（/api/negotiations）。

权限走独立一级域 ``negotiation``，刻意不挂在 ``promotion.`` 下 ——
PR 持有 ``promotion.*:*``，挂过去会让 PR 自动拿到主管的审核权限。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.negotiation.enums import NegotiationStatus
from app.modules.negotiation.schemas import (
    BloggerCooperationHistory,
    NegotiationCreate,
    NegotiationListFilters,
    NegotiationListResponse,
    NegotiationResponse,
    NegotiationReviewRequest,
    NegotiationUpdate,
)
from app.modules.negotiation.service import NegotiationService
from app.modules.promotion.enums import CooperationMode

router = APIRouter(prefix="/api/negotiations", tags=["negotiation"])

SCOPE = "negotiation"
SCOPE_REVIEW = "negotiation.review"


def _svc(session: SessionDep) -> NegotiationService:
    return NegotiationService(session)


@router.post(
    "/",
    response_model=NegotiationResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(SCOPE, "write")],
)
async def create_negotiation(
    payload: NegotiationCreate,
    session: SessionDep,
    user: CurrentActiveUser,
) -> NegotiationResponse:
    """新建谈款单（落草稿）。置换模式的博主服务费会被强制为 0。"""
    return await _svc(session).create(payload, user)


@router.get(
    "/",
    response_model=NegotiationListResponse,
    dependencies=[require_permission(SCOPE, "read")],
)
async def list_negotiations(
    session: SessionDep,
    user: CurrentActiveUser,
    negotiation_status: Annotated[NegotiationStatus | None, Query(alias="status")] = None,
    blogger_id: UUID | None = None,
    style_id: UUID | None = None,
    pr_id: UUID | None = None,
    cooperation_mode: CooperationMode | None = None,
    keyword: Annotated[
        str | None, Query(max_length=64, description="博主昵称 / 款号 / 款名")
    ] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> NegotiationListResponse:
    items, total = await _svc(session).list_negotiations(
        user=user,
        filters=NegotiationListFilters(
            status=negotiation_status,
            blogger_id=blogger_id,
            style_id=style_id,
            pr_id=pr_id,
            cooperation_mode=cooperation_mode,
            keyword=keyword,
        ),
        page=page,
        page_size=page_size,
    )
    return NegotiationListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/status-counts",
    response_model=dict[str, int],
    dependencies=[require_permission(SCOPE, "read")],
)
async def negotiation_status_counts(
    session: SessionDep,
    user: CurrentActiveUser,
) -> dict[str, int]:
    """各状态单据数，给前端 Tab 做角标。"""
    return await _svc(session).status_counts(user)


@router.get(
    "/blogger/{blogger_id}/history",
    response_model=BloggerCooperationHistory,
    dependencies=[require_permission(SCOPE, "read")],
)
async def blogger_cooperation_history(
    blogger_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> BloggerCooperationHistory:
    """博主最近 N 次合作款式，给悬浮卡用。

    取的是推广单而不是谈款单 —— 要看的是实际推了什么、效果如何，
    草稿和被驳回的谈款没真推出去。
    """
    return await _svc(session).blogger_history(blogger_id, user, limit=limit)


@router.get(
    "/{negotiation_id}",
    response_model=NegotiationResponse,
    dependencies=[require_permission(SCOPE, "read")],
)
async def get_negotiation(
    negotiation_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
) -> NegotiationResponse:
    return await _svc(session).get(negotiation_id, user)


@router.put(
    "/{negotiation_id}",
    response_model=NegotiationResponse,
    dependencies=[require_permission(SCOPE, "write")],
)
async def update_negotiation(
    negotiation_id: UUID,
    payload: NegotiationUpdate,
    session: SessionDep,
    user: CurrentActiveUser,
) -> NegotiationResponse:
    """编辑草稿。只有草稿与被驳回的单据能改；改完被驳回的单会回到草稿。"""
    return await _svc(session).update(negotiation_id, payload, user)


@router.post(
    "/{negotiation_id}/submit",
    response_model=NegotiationResponse,
    dependencies=[require_permission(SCOPE, "write")],
)
async def submit_negotiation(
    negotiation_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
) -> NegotiationResponse:
    """提交审核。会清掉上一轮的审核痕迹，避免界面上留着旧的驳回意见。"""
    return await _svc(session).submit(negotiation_id, user)


@router.post(
    "/{negotiation_id}/review",
    response_model=NegotiationResponse,
    dependencies=[require_permission(SCOPE_REVIEW, "approve")],
)
async def review_negotiation(
    negotiation_id: UUID,
    payload: NegotiationReviewRequest,
    session: SessionDep,
    user: CurrentActiveUser,
) -> NegotiationResponse:
    """主管审核。

    通过则在同一事务里生成推广单（建单人记在谈款单的对接 PR 身上，不是审核人）。
    驳回必须填审核意见 —— PR 得知道要改什么。禁止审核自己提交的单。
    """
    return await _svc(session).review(negotiation_id, payload, user)


__all__ = ["router"]
