"""U05 finance 模块 REST API 路由（8 端点 + DELETE 405）。

按 business-logic-model.md 实现：
- 读：list / get / daily-summary × 2
- 状态推进：review / payment-amount / payment-proof / extra-items
- DELETE /settlements/{id} → 405（FB3 财务记录永久不可替换）

全部端点：
- 应用 ``require_permission("settlement:*")``
- 通过 deps 注入 service
- 抛出业务异常 → 全局 error handler 自动映射

降级语义：
- 业务未匹配 → 200 + 空数组（service 层处理）
- 系统失败 → 异常自然冒泡 → 5xx + Sentry
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.modules.auth.deps import (
    CurrentActiveUser,
    require_permission,
)
from app.modules.finance.deps import SettlementServiceDep
from app.modules.finance.enums import SettlementStatus
from app.modules.finance.schemas import (
    DailySummaryActivityResponse,
    DailySummaryAsOfResponse,
    SettlementExtraItemCreateRequest,
    SettlementListFilters,
    SettlementPage,
    SettlementPaymentAmountRequest,
    SettlementPaymentProofRequest,
    SettlementResponse,
    SettlementReviewRequest,
)


router = APIRouter(prefix="/api", tags=["finance"])


def _parse_date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


# ---------------------------------------------------------------------------
# 读查询
# ---------------------------------------------------------------------------


@router.get(
    "/settlements/",
    response_model=SettlementPage,
    dependencies=[require_permission("settlement", "read")],
)
async def list_settlements(
    user: CurrentActiveUser,
    service: SettlementServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=64)] = None,
    settlement_status: SettlementStatus | None = None,
    promotion_id: UUID | None = None,
    blogger_id: UUID | None = None,
    style_id: UUID | None = None,
    pr_id: UUID | None = None,
    reviewed_by: UUID | None = None,
    paid_by: UUID | None = None,
    created_at_from: Annotated[str | None, Query()] = None,
    created_at_to: Annotated[str | None, Query()] = None,
    payment_date_from: Annotated[str | None, Query()] = None,
    payment_date_to: Annotated[str | None, Query()] = None,
    is_my: bool = False,
) -> SettlementPage:
    """EP06 列表 + 多筛选（PR 角色自动限自己提交的）。"""
    filters = SettlementListFilters(
        keyword=keyword,
        settlement_status=settlement_status,
        promotion_id=promotion_id,
        blogger_id=blogger_id,
        style_id=style_id,
        pr_id=pr_id,
        reviewed_by=reviewed_by,
        paid_by=paid_by,
        created_at_from=_parse_date(created_at_from),
        created_at_to=_parse_date(created_at_to),
        payment_date_from=_parse_date(payment_date_from),
        payment_date_to=_parse_date(payment_date_to),
        is_my=is_my,
    )
    return await service.list_settlements(
        filters=filters, page=page, page_size=page_size, user=user
    )


@router.get(
    "/settlements/daily-summary/as-of",
    response_model=DailySummaryAsOfResponse,
    dependencies=[require_permission("settlement", "read")],
)
async def daily_summary_as_of(
    user: CurrentActiveUser,
    service: SettlementServiceDep,
    date: Annotated[str | None, Query()] = None,
) -> DailySummaryAsOfResponse:
    """EP06-S08 口径 B：截至当日各状态快照（FB7）。"""
    return await service.get_daily_summary_as_of(
        date_value=_parse_date(date), user=user
    )


@router.get(
    "/settlements/daily-summary/activity",
    response_model=DailySummaryActivityResponse,
    dependencies=[require_permission("settlement", "read")],
)
async def daily_summary_activity(
    user: CurrentActiveUser,
    service: SettlementServiceDep,
    date: Annotated[str | None, Query()] = None,
) -> DailySummaryActivityResponse:
    """EP06-S08 口径 A：当天发生的动作（FB7）。"""
    return await service.get_daily_summary_activity(
        date_value=_parse_date(date), user=user
    )


@router.get(
    "/settlements/{settlement_id}",
    response_model=SettlementResponse,
    dependencies=[require_permission("settlement", "read")],
)
async def get_settlement(
    settlement_id: UUID,
    user: CurrentActiveUser,
    service: SettlementServiceDep,
) -> SettlementResponse:
    return await service.get_settlement(settlement_id, user)


# ---------------------------------------------------------------------------
# 状态推进
# ---------------------------------------------------------------------------


@router.put(
    "/settlements/{settlement_id}/review",
    response_model=SettlementResponse,
    dependencies=[require_permission("settlement.review", "approve")],
)
async def review_settlement(
    settlement_id: UUID,
    payload: SettlementReviewRequest,
    user: CurrentActiveUser,
    service: SettlementServiceDep,
) -> SettlementResponse:
    """EP06-S03 / S04 PR 主管核查 / 驳回（含自审禁止）。"""
    return await service.review(settlement_id, payload, user)


@router.post(
    "/settlements/{settlement_id}/extra-items",
    response_model=SettlementResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("settlement", "write")],
)
async def add_extra_item(
    settlement_id: UUID,
    payload: SettlementExtraItemCreateRequest,
    user: CurrentActiveUser,
    service: SettlementServiceDep,
) -> SettlementResponse:
    """EP06-S05 增加结算项（运费 / 赞奖）+ total 重算（仅"待付款"状态）。"""
    return await service.add_extra_item(settlement_id, payload, user)


@router.put(
    "/settlements/{settlement_id}/payment-amount",
    response_model=SettlementResponse,
    dependencies=[require_permission("settlement", "write")],
)
async def fill_payment_amount(
    settlement_id: UUID,
    payload: SettlementPaymentAmountRequest,
    user: CurrentActiveUser,
    service: SettlementServiceDep,
) -> SettlementResponse:
    """EP06-S06 PR 主管填写付款金额 → 待财务付款。"""
    return await service.fill_payment_amount(settlement_id, payload, user)


@router.put(
    "/settlements/{settlement_id}/payment-proof",
    response_model=SettlementResponse,
    dependencies=[require_permission("settlement.pay", "upload_proof")],
)
async def upload_payment_proof(
    settlement_id: UUID,
    payload: SettlementPaymentProofRequest,
    user: CurrentActiveUser,
    service: SettlementServiceDep,
) -> SettlementResponse:
    """EP06-S07 财务上传付款截图 → 已付款.

    attachment 6 项强校验（FB4）+ 发 SettlementPaid 反向事件（FB5 通知类）。
    """
    return await service.upload_payment_proof(settlement_id, payload, user)


# ---------------------------------------------------------------------------
# 财务记录永久不可替换（FB3）
# ---------------------------------------------------------------------------


@router.delete(
    "/settlements/{settlement_id}",
    status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
)
async def delete_settlement_not_allowed(settlement_id: UUID) -> None:
    """财务记录永久不可删除（FB3）。

    若需取消未审核的 settlement → 走 reject 路径到"已驳回"。
    若需修正已付款的 settlement → V2 通过 order_adjustment 调整单。
    """
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail={
            "code": "METHOD_NOT_ALLOWED",
            "message": "财务记录不可删除；请走 reject 或 V2 调整单流程",
        },
    )


__all__ = ["router"]
