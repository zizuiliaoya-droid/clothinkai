"""谈款审核 Pydantic Schema。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.negotiation.enums import NegotiationReviewAction, NegotiationStatus
from app.modules.promotion.enums import CooperationMode

_QuoteField = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=10, decimal_places=2),
]


class NegotiationCreate(BaseModel):
    """新建谈款单（落草稿）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    blogger_id: UUID
    style_id: UUID
    goods_main_id: UUID | None = None
    """谈的是哪个商品。不传则生成推广单时由服务端推定主商品（非套装优先）。"""

    cooperation_mode: CooperationMode
    platform: str = Field(default="小红书", min_length=1, max_length=16)
    scheduled_publish_date: date | None = None
    quote_amount: _QuoteField | None = None
    """博主服务费。不传则取 ``blogger.quote``。

    置换模式服务端强制 0（PRD：置换无博主服务费），前端传什么都会被覆盖。
    """

    remark: str | None = None


class NegotiationUpdate(BaseModel):
    """编辑草稿。只有草稿与被驳回的单据能改。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    blogger_id: UUID | None = None
    style_id: UUID | None = None
    goods_main_id: UUID | None = None
    cooperation_mode: CooperationMode | None = None
    """草稿阶段可以改模式 —— PRD 只要求「生成推广单后不可改」，那层锁在推广单上。"""

    platform: str | None = Field(default=None, min_length=1, max_length=16)
    scheduled_publish_date: date | None = None
    quote_amount: _QuoteField | None = None
    remark: str | None = None


class NegotiationReviewRequest(BaseModel):
    """主管审核。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    action: NegotiationReviewAction
    review_opinion: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_opinion_on_reject(self) -> NegotiationReviewRequest:
        if self.action == NegotiationReviewAction.REJECT and not self.review_opinion:
            raise ValueError("驳回时审核意见必填")
        return self


class NegotiationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    blogger_id: UUID
    blogger_nickname: str | None = None
    style_id: UUID
    style_code: str | None = None
    style_name: str | None = None
    goods_main_id: UUID | None = None
    goods_code: str | None = None
    goods_title: str | None = None
    goods_is_suit: bool = False

    pr_id: UUID
    pr_name: str | None = None
    promotion_id: UUID | None = None
    promotion_internal_code: str | None = None
    """审核通过后生成的推广单编码，前端据此跳转到推广管理。"""

    cooperation_mode: str
    platform: str
    scheduled_publish_date: date | None = None
    quote_amount: Decimal | None = None
    """敏感字段，无权限时为 None（与推广单的报价同一套字段权限）。"""

    remark: str | None = None

    status: str
    submitted_at: datetime | None = None
    reviewed_by: UUID | None = None
    reviewer_name: str | None = None
    reviewed_at: datetime | None = None
    review_opinion: str | None = None

    created_at: datetime
    updated_at: datetime


class NegotiationListResponse(BaseModel):
    items: list[NegotiationResponse]
    total: int
    page: int = 1
    page_size: int = 20


class NegotiationListFilters(BaseModel):
    """列表筛选。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    status: NegotiationStatus | None = None
    blogger_id: UUID | None = None
    style_id: UUID | None = None
    pr_id: UUID | None = None
    cooperation_mode: CooperationMode | None = None
    keyword: str | None = Field(default=None, max_length=64)
    """搜博主昵称、款号、款名。"""


# ---------------------------------------------------------------------------
# 博主历史合作（hover 卡）
# ---------------------------------------------------------------------------


class BloggerCooperationItem(BaseModel):
    """博主历史合作款式的一条记录。

    PRD 改动 3 还要求「当时 ROI」，但博主维度的 ROI 在系统里从来没有定义过
    （现有 ROI 都是款式/商品维度，而且要先定「发布后多少天内」这个窗口）。
    所以这里先给已有口径的 CPL（单赞成本）与点赞数 —— 同样能回答「这博主推得怎么样」。
    ROI 窗口口径定下来之后再加快照字段。
    """

    model_config = ConfigDict(from_attributes=True)

    promotion_id: UUID
    internal_code: str
    style_id: UUID
    style_code: str
    style_name: str | None = None
    style_main_image_url: str | None = None
    cooperation_date: date
    cooperation_mode: str | None = None
    publish_status: str
    actual_publish_date: date | None = None
    like_count: int | None = None
    cpl: Decimal | None = None
    """单赞成本 = 博主服务费 ÷ 折算后点赞数。敏感，无报价读权限时为 None。"""

    quote_amount: Decimal | None = None  # 敏感


class BloggerCooperationHistory(BaseModel):
    """某博主最近 N 次合作。"""

    blogger_id: UUID
    total_cooperations: int
    """历史合作总数（不受 limit 影响）。"""

    items: list[BloggerCooperationItem]


__all__ = [
    "BloggerCooperationHistory",
    "BloggerCooperationItem",
    "NegotiationCreate",
    "NegotiationListFilters",
    "NegotiationListResponse",
    "NegotiationResponse",
    "NegotiationReviewRequest",
    "NegotiationUpdate",
]
