"""谈款审核 Pydantic Schema。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from app.modules.negotiation.enums import (
    NegotiationFinalReviewAction,
    NegotiationReviewAction,
    NegotiationStatus,
)
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.schemas import GoodsItemIn, GoodsMemberResponse

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
    """谈的是哪个商品。不传由服务端按现有规则推定主商品（非套装优先）并写回谈款（N1）；
    款式不属于任何商品时留空、按单品建 1 行明细。款式同时属于单品与套装时由前端要求显式选。"""

    cooperation_mode: CooperationMode
    platform: str = Field(default="小红书", min_length=1, max_length=16)
    scheduled_publish_date: date | None = None
    quote_amount: _QuoteField | None = None
    """博主服务费。不传则取 ``blogger.quote``。

    置换模式服务端强制 0（PRD：置换无博主服务费），前端传什么都会被覆盖。
    """

    remark: str | None = None
    items: list[GoodsItemIn] | None = Field(default=None, max_length=10)
    """颜色尺码明细（7.2）。不传 = 按商品成员建空行（单品 1 行、套装每个启用成员 1 行）；
    传了款式集合要与成员完全相同，``sku_id`` 可空（提交时才要求每行都有）。"""


class NegotiationUpdate(BaseModel):
    """编辑（N2）：只在草稿 / 老板退回、本人；改完状态不变，重提走提交。"""

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
    items: list[GoodsItemIn] | None = Field(default=None, max_length=10)
    """编辑时选的颜色尺码（偏差 4）。传了整组替换（款式集合 = 商品成员）；不传时只有换了款式 / 商品才按新成员重建空行。"""


class NegotiationReviewRequest(BaseModel):
    """主管审核。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    action: NegotiationReviewAction
    review_opinion: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_opinion_on_reject(self) -> NegotiationReviewRequest:
        if self.action == NegotiationReviewAction.REJECT and not self.review_opinion:
            # PydanticCustomError 而不是 ValueError：后者把异常对象放进 ctx，422 响应序列化时变 500
            raise PydanticCustomError("review_opinion_required", "驳回时审核意见必填")
        return self


class NegotiationFinalReviewRequest(BaseModel):
    """老板审核（N6 定稿 / N7 退回，7.2）。退回时意见必填（schema 层）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    action: NegotiationFinalReviewAction
    opinion: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_opinion_on_return(self) -> NegotiationFinalReviewRequest:
        if self.action == NegotiationFinalReviewAction.RETURN and not self.opinion:
            raise PydanticCustomError("final_opinion_required", "退回时意见必填")
        return self


_ReceiverNameField = Annotated[str | None, Field(max_length=32)]
_ReceiverPhoneField = Annotated[str | None, Field(max_length=32)]
_ReceiverAddressField = Annotated[str | None, Field(max_length=255)]


class NegotiationReceiverUpdate(BaseModel):
    """保存收货信息（N8，``PUT /{id}/receiver``）：只改这次传了的项（``model_fields_set``）。
    限长 32 / 32 / 255（同推广单列宽）；去空白后空串 = 清空；电话传了就校验并规范化（422 ``INVALID_RECEIVER_PHONE``）。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    receiver_name: _ReceiverNameField = None
    receiver_phone: _ReceiverPhoneField = None
    receiver_address: _ReceiverAddressField = None


class NegotiationReceiverConfirm(BaseModel):
    """确认收货信息（N9，``POST /{id}/confirm-receiver``）：以 body 为准（与已保存的不同也行）。
    三项非空、电话格式不在 schema 判：按矩阵 ★⁴ 一次全报（422 ``FLOW_GATE_MISSING``，键与 ``ui`` 相同）。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    receiver_name: _ReceiverNameField = None
    receiver_phone: _ReceiverPhoneField = None
    receiver_address: _ReceiverAddressField = None


class NegotiationReassignRequest(BaseModel):
    """管理员转交（N12，``POST /{id}/reassign``，11-57）：换对接 PR。原因 1 ~ 500 字（7.1，去空白后判）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    pr_id: UUID
    reason: str = Field(min_length=1, max_length=500)


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


class OthersInProgressItem(BaseModel):
    """同一博主有别的 PR 在谈（7c-16）：只给同事姓名、状态、更新时间，不给 id 与内容。"""

    pr_name: str
    status: str
    updated_at: datetime


class NegotiationGoodsMembers(BaseModel):
    """``GET /goods-members``（偏差 3）：新建 / 换商品时明细该有哪几行。

    ``goods_main_id`` = 传入的，或不传时按 N1 推定的（款式不属于任何商品为 null）；``members`` 口径同 ``members_of``。
    """

    goods_main_id: UUID | None = None
    members: list[GoodsMemberResponse]


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
    others_in_progress: list[OthersInProgressItem] | None = None
    """只在新建与提交的响应里：同博主别的 PR 在谈的单（只提示不拦，7.2）；其余接口为 null。"""
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
    "NegotiationFinalReviewRequest",
    "NegotiationGoodsMembers",
    "NegotiationItemResponse",
    "NegotiationListFilters",
    "NegotiationListResponse",
    "NegotiationReassignRequest",
    "NegotiationReceiverConfirm",
    "NegotiationReceiverUpdate",
    "NegotiationResponse",
    "NegotiationReviewRequest",
    "NegotiationUpdate",
    "OthersInProgressItem",
    "ReceiverSuggestion",
]
