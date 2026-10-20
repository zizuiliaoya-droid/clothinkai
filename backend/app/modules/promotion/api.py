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

from fastapi import APIRouter, Body, File, Form, Query, UploadFile, status
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
    ShipStatus,
)
from app.modules.promotion.schemas import (
    GoodsItemIn,
    PromotionAmountLogResponse,
    PromotionCancelRequest,
    PromotionCreate,
    PromotionListFilters,
    PromotionMetricsRequest,
    PromotionPage,
    PromotionPaymentQrBindRequest,
    PromotionPaymentQrUploadInitRequest,
    PromotionPaymentQrUploadInitResponse,
    PromotionPublishRequest,
    PromotionRecallResultRequest,
    PromotionRecallStartRequest,
    PromotionResponse,
    PromotionResubmitRequest,
    PromotionReturnWaybillRequest,
    PromotionReviewRequest,
    PromotionSettlementResubmitRequest,
    PromotionUpdate,
    RetrospectiveResponse,
    RetrospectiveWriteRequest,
    ShipStatusFilter,
)

router = APIRouter(prefix="/api", tags=["promotion"])


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post(
    "/promotions/",
    response_model=PromotionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("promotion_create", "write")],
)
async def create_promotion(
    payload: PromotionCreate,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S02 直接新建推广 + 自动 internal_code + 重复检测.

    流程线 PR-5（4.4）：挂 ``promotion_create:write``（只授主管，管理员靠 ``*``），PR 不能直接建单，
    推广单只从谈款生成（Q6）。
    ``need_shipping`` 默认 false：直接新建是补录历史用，``ship_status`` 保持 NULL、不进待推送队列；
    勾了才写「待发货」（流程线 S1，11-58）。
    """
    return await service.create_promotion(
        payload,
        user,
        ship_status=ShipStatus.PENDING if payload.need_shipping else None,
        reviewed_negotiation_copy=False,
    )


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
    ship_status: ShipStatusFilter | None = None,
) -> PromotionPage:
    """EP05-S03 / S05 / S06 列表 + CTE 衍生字段（urge_status / dual_platform）.

    ``ship_status``：待发货（只算阶段「待推送仓库」）/ 待打单 / 已发货 / ``none``（历史单）。
    060 前的 ``has_print_address`` / ``has_waybill`` 已删（旧前端传了 FastAPI 忽略、不过滤）。
    """
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
        ship_status=ship_status,
    )
    return await service.list_promotions(filters=filters, page=page, page_size=page_size, user=user)


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


@router.put(
    "/promotions/{promotion_id}/items",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def replace_promotion_items(
    promotion_id: UUID,
    items: Annotated[list[GoodsItemIn], Body(max_length=10)],
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """整组替换颜色尺码明细（流程线 7.3）；能不能改由矩阵 ``goods_items`` 定，不是「改」→ 403。"""
    return await service.replace_items(promotion_id, items, user)


@router.post(
    "/promotions/{promotion_id}/payment-qr/upload-init",
    response_model=PromotionPaymentQrUploadInitResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("promotion.payment_qr", "write")],
)
async def init_payment_qr_upload(
    promotion_id: UUID,
    payload: PromotionPaymentQrUploadInitRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionPaymentQrUploadInitResponse:
    return await service.init_payment_qr_upload(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/payment-qr/upload",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion.payment_qr", "write")],
)
async def upload_payment_qr(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    file: Annotated[UploadFile, File(description="JPG、PNG 或 WebP 收款码图片")],
) -> PromotionResponse:
    """由后端代传收款码到私有 R2，避免浏览器依赖 bucket CORS。"""
    try:
        data = await file.read(10 * 1024 * 1024 + 1)
    finally:
        await file.close()
    return await service.upload_payment_qr(
        promotion_id,
        filename=file.filename,
        mime_type=file.content_type,
        data=data,
        user=user,
    )


@router.put(
    "/promotions/{promotion_id}/payment-qr",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion.payment_qr", "write")],
)
async def bind_payment_qr(
    promotion_id: UUID,
    payload: PromotionPaymentQrBindRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    return await service.bind_payment_qr(promotion_id, payload, user)


@router.delete(
    "/promotions/{promotion_id}/payment-qr",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("promotion.payment_qr", "write")],
)
async def remove_payment_qr(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> Response:
    await service.remove_payment_qr(promotion_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/promotions/{promotion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("promotion_create", "write")],
)
async def delete_promotion(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> Response:
    """软停用（与状态机正交：is_active=false）.

    流程线 PR-5（4.4、11-15）：路由挂 ``promotion_create:write``，service 再按矩阵 ``deactivate`` 判
    （结款单生成后只有管理员能停用）。
    """
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
    payload: PromotionRecallResultRequest,
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
    payload: PromotionRecallResultRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S09 召回失败（可重新发起）."""
    return await service.recall_failure(promotion_id, user)


@router.post(
    "/promotions/{promotion_id}/review",
    response_model=PromotionResponse,
    # 独立一级域（流程线 4.3，默 6）：PR 的 promotion.*:* 命中不了，PR 完全没有审核权
    dependencies=[require_permission("promotion_review", "approve")],
)
async def review_promotion(
    promotion_id: UUID,
    payload: PromotionReviewRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """EP05-S13 PR 主管审核（approve / reject），流程线 3.4 R2 ~ R4。

    approve 按应付分流（与合作模式无关）：

    - 服务费 = 0：直接已结款（R3），写 ``settled_at``，不发 SettlementRequested、不建结款单
    - 服务费 > 0：要收款码（缺 → 422 ``FLOW_GATE_MISSING``），发 SettlementRequested，
      结款单直接建在待付款（R2）
    - 已有导入的已付款结款单：推广单直接记已付款、不发事件；已有其他状态的结款单 → 409
      ``SETTLEMENT_ALREADY_EXISTS``（R2 ⑥）
    - 寄拍要有寄回单号（或召回成功且有召回寄回截图），缺 → 422 ``FLOW_GATE_MISSING``

    响应带 ``review_outcome``。驳回要同时给 review_reason 与 review_reason_category（三选一）；
    通过时不改这两列（保留最近一次驳回，7a-4）。禁止自审 → 403 ``SELF_REVIEW_FORBIDDEN``。
    """
    return await service.review(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/resubmit",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def resubmit_promotion(
    promotion_id: UUID,
    payload: PromotionResubmitRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """7a-4 驳回后重新提交：已驳回 → 待核查，等主管再审.

    - 只允许从「已驳回」出发（其余状态 422 ILLEGAL_STATE_TRANSITION），且要求已发布
    - 重提说明 note 必填（≤ 2000 字）；可同时改发布链接 / 实际发布日期，不传不动
    - 实际发布日期不能晚于今天（422 PUBLISH_DATE_IN_FUTURE）
    - 上一轮驳回原因保留，不清
    """
    return await service.resubmit(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/settlement/resubmit",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def resubmit_settlement(
    promotion_id: UUID,
    payload: PromotionSettlementResubmitRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """流程线 R7 结款重提：财务驳回结款单后，推广单 结款驳回 → 待付款，结款单 已驳回 → 待付款.

    - 只在推广单 = 结款驳回（其余 422 ILLEGAL_STATE_TRANSITION）；要有收款码（缺 → 422 FLOW_GATE_MISSING）
    - 说明 note 必填（≤ 2000 字），进时间线
    - 同事务发 ``SettlementResubmitted``：结款单清空实付与调整说明，由财务重新填；结款单不在已驳回 → 409，整笔回滚
    - 与 7a 的 ``/resubmit`` 分开：那个是推广被主管驳回
    """
    return await service.resubmit_settlement(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/settlement-chat",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def upload_settlement_chat(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    file: Annotated[UploadFile, File(description="结款聊天截图（JPG / PNG / WebP）")],
) -> PromotionResponse:
    """流程线 R9 上传结款聊天截图：财务付款后，推广单 已付款 → 已结款.

    - 只在推广单 = 已付款（其余 422 ILLEGAL_STATE_TRANSITION）；截图必传（不合格 422
      SETTLEMENT_CHAT_SCREENSHOT_REQUIRED）
    - 同事务写截图、``settled_at`` 与时间线 ``promotion.settled``；后端代传，失败删对象
    """
    try:
        data = await file.read(10 * 1024 * 1024 + 1)
    finally:
        await file.close()
    return await service.upload_settlement_chat(
        promotion_id,
        filename=file.filename,
        mime_type=file.content_type,
        data=data,
        user=user,
    )


@router.post(
    "/promotions/{promotion_id}/return-waybill",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def set_return_waybill(
    promotion_id: UUID,
    payload: PromotionReturnWaybillRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """上传博主寄回衣服单号。

    寄拍模式审核通过的前提。与仓库发货单号是两个方向：那个寄给博主，这个博主寄回来。
    """
    return await service.set_return_waybill(promotion_id, payload, user)


@router.post(
    "/promotions/{promotion_id}/brand-comment",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def upload_brand_comment(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    file: Annotated[UploadFile, File(description="品牌词评论截图（JPG / PNG / WebP）")],
) -> PromotionResponse:
    """上传品牌词评论截图（PRD 改动 5，提交发布审核的前提）。

    不限状态：PR 可能发布前就截好了，也可能被 publish 挡住之后才来补。
    真正的门槛在 publish —— 没有截图提交不了发布审核。
    """
    try:
        data = await file.read(10 * 1024 * 1024 + 1)
    finally:
        await file.close()
    return await service.upload_brand_comment(
        promotion_id,
        filename=file.filename,
        mime_type=file.content_type,
        data=data,
        user=user,
    )


@router.get(
    "/promotions/{promotion_id}/amount-log",
    response_model=list[PromotionAmountLogResponse],
    dependencies=[require_permission("promotion", "read")],
)
async def promotion_amount_log(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[PromotionAmountLogResponse]:
    """金额变更时间线（PRD 第 10 节第 14 条：成本修改可追溯）。

    这里的 `require_permission("promotion", "read")` 只是粗粒度闸门，**真正的门控在
    service 里的字段级判定** —— 运营持 `promotion.*:read`，任何
    `promotion.xxx:read` 都会被通配命中，所以不能靠新建 scope 挡住他们。
    """
    return await service.amount_log(promotion_id, user, limit=limit)


# ---------------------------------------------------------------------------
# 复盘（PRD V1.4 改动 4）
# ---------------------------------------------------------------------------


@router.post(
    "/promotions/{promotion_id}/metrics",
    response_model=PromotionResponse,
    # 流程线 3.7：从 promotion.retro:write 改名，PR / 主管的 promotion.*:* 照样命中，不入册、不授权
    dependencies=[require_permission("promotion.metrics", "write")],
)
async def record_metrics(
    promotion_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    like_count: Annotated[int, Form(ge=0)],
    collect_count: Annotated[int, Form(ge=0)],
    comment_count: Annotated[int, Form(ge=0)],
    screenshot: Annotated[UploadFile, File(description="7 天数据截图")],
) -> PromotionResponse:
    """录 7 天数据（已结款且发文满 7 天，只录一次，不推进复盘）。

    门槛不满足 → 422 ``METRICS_NOT_AVAILABLE_YET``（带 ``available_from``、``blocked_by``）。走 multipart
    是因为截图必传，三个指标和图得在同一个请求里 —— 分两步会出现「数字录了图没传」
    的中间态。
    """
    try:
        data = await screenshot.read(10 * 1024 * 1024 + 1)
    finally:
        await screenshot.close()
    return await service.record_metrics(
        promotion_id,
        PromotionMetricsRequest(
            like_count=like_count,
            collect_count=collect_count,
            comment_count=comment_count,
        ),
        user,
        screenshot=(screenshot.filename, screenshot.content_type, data),
    )


@router.post(
    "/promotions/{promotion_id}/retrospective",
    response_model=PromotionResponse,
    dependencies=[require_permission("promotion_retro", "write")],
)
async def write_retrospective(
    promotion_id: UUID,
    payload: RetrospectiveWriteRequest,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
) -> PromotionResponse:
    """主管写完即完成；已完成版仅持*者可追加修改，旧版留档。"""
    return await service.write_retrospective(promotion_id, payload, user)


@router.get(
    "/bloggers/{blogger_id}/retrospectives",
    response_model=list[RetrospectiveResponse],
    dependencies=[require_permission("promotion", "read")],
)
async def blogger_retrospectives(
    blogger_id: UUID,
    user: CurrentActiveUser,
    service: PromotionServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[RetrospectiveResponse]:
    """某博主的历史复盘，倒序（PRD：hover 卡展示该博主所有历史复盘）。

    只返回主管确认过的 —— 没过确认的是草稿，进博主档案会误导下次选博主的人。
    """
    return await service.blogger_retrospectives(blogger_id, user, limit=limit)


__all__ = ["router"]
