"""U04 promotion 模块 REST API 路由（11 端点）。

按 business-logic-model.md 实现：
- CRUD: 4 端点（create / list / get / update / soft_delete）
- 状态推进: 6 端点（publish / cancel / start_recall / recall_success / recall_failure / review）

全部端点：
- 应用 ``require_permission("promotion:*")``
- 通过 deps 注入 service
- 抛出业务异常 → 全局 error handler 自动映射

降级语义：
- 业务未匹配 → 200 + 空数组（service 层处理）
- 系统失败 → 异常自然冒泡 → 5xx + Sentry
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from fastapi.responses import Response

from app.modules.auth.deps import (
    CurrentActiveUser,
    require_permission,
)
from app.modules.promotion.deps import PromotionServiceDep
from app.modules.promotion.enums import (
    PublishStatus,
    RecallStatus,
    SettlementStatus,
)
from app.modules.promotion.schemas import (
    PromotionCancelRequest,
    PromotionCreate,
    PromotionListFilters,
    PromotionPage,
    PromotionPublishRequest,
    PromotionRecallResultRequest,
    PromotionRecallStartRequest,
    PromotionResponse,
    PromotionReviewRequest,
    PromotionUpdate,
)


router = APIRouter(prefix="/api", tags=["promotion"])


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/promotions/",
    response_model=PromotionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("promotion", "write")],
)
async def create_promotion(
    payload: PromotionCreate,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S02 PR 创建推广 + 自动 internal_code + 重复检测."""
    return await service.create_promotion(payload, user)


@router.get(
    "/promotions/",
    response_model=PromotionPage,
    dependencies=[require_permission("promotion", "read")],
)
async def list_promotions(
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=64)] = None,
    publish_status: PublishStatus | None = None,
    recall_status: RecallStatus | None = None,
    settlement_status: SettlementStatus | None = None,
    platform: Annotated[str | None, Query(max_length=16)] = None,
    blogger_id: UUID | None = None,
    style_id: UUID | None = None,
    pr_id: UUID | None = None,
    cooperation_date_from: Annotated[str | None, Query()] = None,
    cooperation_date_to: Annotated[str | None, Query()] = None,
    scheduled_publish_date_from: Annotated[str | None, Query()] = None,
    scheduled_publish_date_to: Annotated[str | None, Query()] = None,
    is_active: bool | None = True,
    only_dual_platform: bool = False,
    is_hit: bool | None = None,
) -> PromotionPage:
    """EP05-S03 / S05 / S06 列表 + CTE 衍生字段（urge_status / dual_platform）."""
    from datetime import date

    def _parse_date(s: str | None) -> date | None:
        return date.fromisoformat(s) if s else None

    filters = PromotionListFilters(
        keyword=keyword,
        publish_status=publish_status,
        recall_status=recall_status,
        settlement_status=settlement_status,
        platform=platform,
        blogger_id=blogger_id,
        style_id=style_id,
        pr_id=pr_id,
        cooperation_date_from=_parse_date(cooperation_date_from),
        cooperation_date_to=_parse_date(cooperation_date_to),
        scheduled_publish_date_from=_parse_date(scheduled_publish_date_from),
        scheduled_publish_date_to=_parse_date(scheduled_publish_date_to),
        is_active=is_active,
        only_dual_platform=only_dual_platform,
        is_hit=is_hit,
    )
    return await service.list_promotions(
        filters=filters, page=page, page_size=page_size, user=user
    )


@router.get(
    "/promotions/{promotion_id}",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "read")],
)
async def get_promotion(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    return await service.get_promotion(promotion_id, user)


@router.patch(
    "/promotions/{promotion_id}",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def update_promotion(
    promotion_id: UUID,
    payload: PromotionUpdate,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """编辑推广（PATCH 语义；状态字段不在此改）."""
    return await service.update_promotion(promotion_id, payload, user)


@router.delete(
    "/promotions/{promotion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("promotion", "delete")],
)
async def delete_promotion(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> Response:
    """软停用（与状态机正交：is_active=false）."""
    await service.soft_delete_promotion(promotion_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# 状态推进（6 端点）
# ---------------------------------------------------------------------------


@router.post(
    "/promotions/{promotion_id}/publish",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def publish_promotion(
    promotion_id: UUID,
    payload: PromotionPublishRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S07 发布（同事务推进 settlement_status: 未核查→待核查 + 发 PromotionPublished 事件）."""
    return await service.publish(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/cancel",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def cancel_promotion(
    promotion_id: UUID,
    payload: PromotionCancelRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S08 取消（仅 publish_status='未发布' 允许；已发布需走召回）."""
    return await service.cancel(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/recall/start",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def start_recall_promotion(
    promotion_id: UUID,
    payload: PromotionRecallStartRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S09 启动召回（要求 publish_status ∈ {已发布, 已取消}）."""
    return await service.start_recall(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/recall/success",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def recall_success(
    promotion_id: UUID,
    payload: PromotionRecallResultRequest,  # noqa: ARG001  -- 预留 remark
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S09 召回成功（终态）."""
    return await service.recall_success(promotion_id, user)


@router.post(
    "/promotions/{promotion_id}/recall/failure",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def recall_failure(
    promotion_id: UUID,
    payload: PromotionRecallResultRequest,  # noqa: ARG001  -- 预留 remark
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S09 召回失败（可重新发起）."""
    return await service.recall_failure(promotion_id, user)


@router.post(
    "/promotions/{promotion_id}/review",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion.review", "approve")],
)
async def review_promotion(
    promotion_id: UUID,
    payload: PromotionReviewRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S13 PR 主管审核（approve / reject）.

    approve 时同事务发 SettlementRequested 事件（U05 监听创建 settlement）。
    禁止自审（reviewer != pr_id）。
    """
    return await service.review(promotion_id, payload, user)


__all__ = ["router"]
