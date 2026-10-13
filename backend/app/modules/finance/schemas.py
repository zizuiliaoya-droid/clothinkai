"""U05 finance 模块 Pydantic Schemas。

字段命名映射到 ORM；service 层 ``to_response`` 时按角色过滤敏感字段
（amount / total_amount / payment_amount / payment_proof_signed_url）。

Schema 列表（12+）：
- SettlementResponse / SettlementPage / SettlementListFilters
- SettlementReviewRequest / SettlementExtraItemCreateRequest
- SettlementPaymentAmountRequest / SettlementPaymentProofRequest
- SettlementExtraItemResponse
- DailySummaryAsOfResponse / DailySummaryActivityResponse
- AmountBucket（汇总响应公共结构）
- PromotionDuplicateWarning（仅创建时返回，但 settlement 创建不通过 HTTP）
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from app.modules.finance.enums import ExtraItemType, SettlementStatus
from app.modules.promotion.enums import ReviewAction

_AmountField = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=12, decimal_places=2),
]

_PositiveAmountField = Annotated[
    Decimal,
    Field(gt=Decimal("0"), max_digits=12, decimal_places=2),
]


# ---------------------------------------------------------------------------
# 状态推进入参
# ---------------------------------------------------------------------------


class SettlementReviewRequest(BaseModel):
    """审核入参（approve / reject）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    action: ReviewAction
    review_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_reason_on_reject(self) -> SettlementReviewRequest:
        if self.action == ReviewAction.REJECT and not self.review_reason:
            raise ValueError("驳回时 review_reason 必填")
        return self


class SettlementPaymentAmountRequest(BaseModel):
    """fill_payment 入参（F3，设计 3.4、7.5）。

    实付 ≥ 0（0 = 全额扣除，11-63）；实付 ≠ 应付（``total_amount``）时 ``payment_adjust_note`` 必填，
    在 service 层判（422 ``FLOW_GATE_MISSING``，key = ``payment_adjust_note``），相等时可空。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    payment_amount: _AmountField
    payment_adjust_note: str | None = Field(default=None, max_length=2000)


class SettlementPaymentProofRequest(BaseModel):
    """mark_paid 入参（财务上传付款截图）。

    ``payment_date`` 只为兼容旧客户端的请求体保留：**忽略此字段**，付款日期一律取服务端的
    北京时间当天（F4，Q11；照推广建单 ``cooperation_date`` 的兼容写法）。
    """

    model_config = ConfigDict()

    payment_date: date | None = None
    payment_proof_attachment_id: UUID


class SettlementResubmitRequest(BaseModel):
    """resubmit 入参（已驳回 → 待核查）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    remark: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# Extra Item
# ---------------------------------------------------------------------------


class SettlementExtraItemCreateRequest(BaseModel):
    """增加结算项（运费 / 赞奖 / 其他）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    item_type: ExtraItemType
    amount: _PositiveAmountField
    remark: str | None = Field(default=None, max_length=255)


class SettlementExtraItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    settlement_id: UUID
    item_type: str
    amount: Decimal
    remark: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# 响应 / 列表
# ---------------------------------------------------------------------------


class SettlementResponse(BaseModel):
    """结算单响应。

    敏感字段（amount / total_amount / payment_amount / payment_proof_signed_url）
    按角色过滤（详见 ``service.SettlementService.to_response``）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    settlement_no: str
    promotion_id: UUID
    blogger_id: UUID
    style_id: UUID
    pr_id: UUID | None = None

    # 金额（敏感）
    amount: Decimal | None = None  # 敏感
    total_amount: Decimal | None = None  # 敏感
    payment_amount: Decimal | None = None  # 敏感

    # 付款相关
    payment_date: date | None = None
    payment_proof_attachment_id: UUID | None = None
    payment_proof_signed_url: str | None = None  # service 层填入（敏感）

    # 业务字段
    note_title: str | None = None
    remark: str | None = None

    # 状态
    settlement_status: str

    # 审核
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    review_action: str | None = None
    review_reason: str | None = None

    # 财务付款
    paid_by: UUID | None = None

    # 通用
    created_at: datetime
    updated_at: datetime

    # 反范式展示字段（list 时 join 填充，对齐 final.xlsx 结款表）
    style_code: str | None = None
    style_name: str | None = None
    blogger_nickname: str | None = None

    # 子表关联
    extra_items: list[SettlementExtraItemResponse] = Field(default_factory=list)


class SettlementPage(BaseModel):
    items: list[SettlementResponse]
    total: int
    page: int
    page_size: int
    ui: dict[str, Any] = Field(default_factory=lambda: {"actions": {}})
    """页级 ``ui.actions.import``：能上传结款单（``manual_settlement``，即 ``finance.settlement:pay``）才有
    （流程线 PR-5，7.5）；结款单行级 ``ui`` 随 PR-6。"""


# ---------------------------------------------------------------------------
# 列表 filter
# ---------------------------------------------------------------------------


class SettlementListFilters(BaseModel):
    """列表过滤入参（query string 解析后构造）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    keyword: str | None = Field(default=None, max_length=64)
    settlement_status: SettlementStatus | None = None
    promotion_id: UUID | None = None
    blogger_id: UUID | None = None
    style_id: UUID | None = None
    pr_id: UUID | None = None
    reviewed_by: UUID | None = None
    paid_by: UUID | None = None
    created_at_from: date | None = None
    created_at_to: date | None = None
    payment_date_from: date | None = None
    payment_date_to: date | None = None
    amount_from: Decimal | None = None
    amount_to: Decimal | None = None
    payment_amount_from: Decimal | None = None
    payment_amount_to: Decimal | None = None
    is_my: bool = False  # PR 角色限自己提交（service 层强制）


# ---------------------------------------------------------------------------
# 双口径汇总响应（FB7）
# ---------------------------------------------------------------------------


class AmountBucket(BaseModel):
    """汇总响应公共结构：count + total_amount。"""

    model_config = ConfigDict()

    count: int = Field(ge=0)
    total_amount: Decimal


class DailySummaryAsOfBuckets(BaseModel):
    """as_of 口径的 5 个 bucket（按 settlement_status 分组）。"""

    model_config = ConfigDict()

    pending_review: AmountBucket
    pending_payment: AmountBucket
    pending_finance: AmountBucket
    paid: AmountBucket
    rejected: AmountBucket


class DailySummaryAsOfResponse(BaseModel):
    """口径 B：截至当日各状态快照（FB7）。"""

    model_config = ConfigDict()

    kind: str = "as_of"
    date: date
    as_of: DailySummaryAsOfBuckets
    outstanding_total: AmountBucket  # = pending_review + pending_payment + pending_finance


class DailySummaryActivityBuckets(BaseModel):
    """activity 口径的 4 个 bucket（按当天动作分组）。"""

    model_config = ConfigDict()

    newly_created: AmountBucket
    newly_approved: AmountBucket
    newly_paid: AmountBucket
    newly_rejected: AmountBucket


class DailySummaryActivityResponse(BaseModel):
    """口径 A：当天发生的动作（FB7 含 audit_log JOIN）。"""

    model_config = ConfigDict()

    kind: str = "activity"
    date: date
    activity: DailySummaryActivityBuckets


__all__ = [
    "AmountBucket",
    "DailySummaryActivityBuckets",
    "DailySummaryActivityResponse",
    "DailySummaryAsOfBuckets",
    "DailySummaryAsOfResponse",
    "SettlementExtraItemCreateRequest",
    "SettlementExtraItemResponse",
    "SettlementListFilters",
    "SettlementPage",
    "SettlementPaymentAmountRequest",
    "SettlementPaymentProofRequest",
    "SettlementResponse",
    "SettlementResubmitRequest",
    "SettlementReviewRequest",
]
