"""寄回运费的独立请求/响应；不接受服务费结款字段或客户端付款日期。"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

FreightStatus = Literal["待审核", "待付款", "已付款", "已驳回"]
FreightAmount = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
Reason = Annotated[str, Field(min_length=1, max_length=2000)]


class FreightRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class FreightSubmitRequest(FreightRequest):
    amount: FreightAmount | None = None


class FreightResubmitRequest(FreightSubmitRequest):
    note: Reason


class FreightReviewRequest(FreightRequest):
    action: Literal["approve", "reject"]
    reason: Reason | None = None

    @model_validator(mode="after")
    def reject_needs_reason(self) -> Self:
        if self.action == "reject" and self.reason is None:
            raise ValueError("驳回原因必填")
        return self


class FreightRejectRequest(FreightRequest):
    reason: Reason


class FreightListFilters(BaseModel):
    status: FreightStatus | None = None
    promotion_id: UUID | None = None
    keyword: str | None = Field(default=None, max_length=64)
    payment_date_from: date | None = None
    payment_date_to: date | None = None


class ReturnFreightPaymentResponse(BaseModel):
    id: UUID
    promotion_id: UUID
    line_id: UUID
    promotion_internal_code: str
    blogger_nickname: str
    display_short_name: str | None
    amount: Decimal | None
    status: FreightStatus
    payment_qr_attachment_id: UUID | None = None
    waybill_attachment_id: UUID | None = None
    payment_proof_attachment_id: UUID | None = None
    payment_qr_signed_url: str | None = None
    waybill_signed_url: str | None = None
    payment_proof_signed_url: str | None = None
    submitted_by: UUID | None
    submitted_by_name: str | None = None
    submitted_at: datetime
    reviewed_by: UUID | None = None
    reviewed_by_name: str | None = None
    reviewed_at: datetime | None = None
    rejected_by: UUID | None = None
    rejected_by_name: str | None = None
    rejected_at: datetime | None = None
    reject_reason: str | None = None
    paid_by: UUID | None = None
    paid_by_name: str | None = None
    paid_at: datetime | None = None
    payment_date: date | None = None
    remark: str | None = None
    ui: dict[str, Any] = Field(default_factory=dict)


class ReturnFreightPaymentPage(BaseModel):
    items: list[ReturnFreightPaymentResponse]
    total: int
    page: int
    page_size: int


class ReturnFreightPaymentBrief(BaseModel):
    """推广列表/详情的同形摘要；图片按需读取R33单笔接口。"""

    id: UUID
    status: FreightStatus
    amount: Decimal | None = None
    payment_date: date | None = None
    reject_reason: str | None = None
    ui: dict[str, Any] = Field(default_factory=dict)
