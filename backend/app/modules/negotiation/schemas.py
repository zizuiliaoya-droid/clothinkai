"""谈款审核 Pydantic Schema。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
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


class NegotiationItemResponse(BaseModel):
    """谈款商品明细一行（7.2）。颜色尺码实时读 SKU；``sku_id`` 为空 = 还没选。"""

    id: UUID
    style_id: UUID
    style_code: str
    """搜索、导出用，界面不显示。"""
    display_short_name: str
    """款式简称，没填（或全空白）回落款式名（同推广单明细）。"""
    goods_title: str
    """款式全称（悬停显示）。"""
    sku_id: UUID | None = None
    color: str | None = None
    size: str | None = None
    style_main_image_url: str | None = None
    """该行款式主图的签名 URL；没有图或签名失败为 null。"""


class ReceiverSuggestion(BaseModel):
    """待补收货地址时的预填（3.2）：该博主最近一张有收件信息的推广单，没有再取最近一张已定稿谈款。只做预填。"""

    receiver_name: str | None = None
    receiver_phone: str | None = None
    receiver_address: str | None = None
    source: Literal["promotion", "negotiation"]
    source_label: str
    """如「推广单 DE2610030A1 · 10-03」「谈款定稿 · 10-02」（日期按北京时间）。"""


class NegotiationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    line_id: UUID
    """主线号（开抽屉、读时间线用）。"""
    copied_from_id: UUID | None = None
    """「复制为新谈款」的来源。"""
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
    final_reviewed_by: UUID | None = None
    final_reviewer_name: str | None = None
    final_reviewed_at: datetime | None = None
    final_review_opinion: str | None = None

    receiver_name: str | None = None
    receiver_phone: str | None = None
    receiver_address: str | None = None
    """收件三项逐项过字段规则 ``promotion.receiver_*``（4.6），财务、运营拿到 null。"""
    receiver_confirmed_at: datetime | None = None
    receiver_confirmed_by: UUID | None = None

    display_short_name: str | None = None
    """品名：商品简称 → 款式简称 → 款式名（偏差 6）。"""
    items: list[NegotiationItemResponse] = Field(default_factory=list)
    overdue: bool = False
    """主管待审且等待超过 36 小时（按实际时长比，7c-6）；看不到超时高亮的人恒为 false。"""
    overdue_hours: int | None = None
    """主管待审已等待的整小时数；其余状态、看不到超时高亮的人为 null。"""
    receiver_suggestion: ReceiverSuggestion | None = None
    """只在详情、待补收货地址、当前用户能录收货信息时有。"""
    ui: dict[str, Any] | None = None
    """矩阵 ``ui``（7.1）：列表行 actions + edits，详情 actions + fields。"""

    created_at: datetime
    updated_at: datetime


class NegotiationListResponse(BaseModel):
    items: list[NegotiationResponse]
    total: int
    page: int = 1
    page_size: int = 20
    ui: dict[str, Any] = Field(default_factory=lambda: {"actions": {}})
    """页级 ``ui.actions``：``create``（新建谈款，偏差 8）。"""


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
    overdue_only: bool = False
    """只看主管待审超 36 小时的（「超时」Tab）。"""


# ---------------------------------------------------------------------------
# 博主历史合作（hover 卡）
# ---------------------------------------------------------------------------


class BloggerCooperationItem(BaseModel):
    """博主历史合作款式的一条记录。

    PRD 改动 3 原文有「当时 ROI」，业务方 10-02 确认博主卡片不算 ROI，只看单篇点赞成本。
    """

    model_config = ConfigDict(from_attributes=True)

    promotion_id: UUID
    internal_code: str
    style_id: UUID
    style_code: str
    style_name: str | None = None
    """建单时的款式简称快照（接口兼容保留）。界面显示 ``display_short_name``。"""
    display_short_name: str | None = None
    """品名：归属商品的简称，没填回落快照（7a-8，规则见 ``promotion/display_name.py``）。"""
    goods_title: str | None = None
    """归属商品全称（悬停提示用）。没有归属商品为 None。"""
    style_main_image_url: str | None = None
    cooperation_date: date
    cooperation_mode: str | None = None
    publish_status: str
    actual_publish_date: date | None = None
    like_count: int | None = None
    cpl: Decimal | None = None
    """单篇点赞成本 = 总推广成本 ÷ 折算后 7 天点赞数，录过 7 天数据才有值。

    敏感：要同时有报价与成本的读权限，否则为 None。
    """

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
    "NegotiationItemResponse",
    "NegotiationListFilters",
    "NegotiationListResponse",
    "NegotiationResponse",
    "NegotiationReviewRequest",
    "NegotiationUpdate",
    "ReceiverSuggestion",
]
