"""U04 promotion 模块 Pydantic Schemas。

字段命名映射到 ORM；service 层 ``to_response`` 时按角色过滤敏感字段
（quote_amount / cost_snapshot / cpl）。

Schema 列表（13）：
- PromotionBase / PromotionCreate / PromotionUpdate
- PromotionPublishRequest / PromotionCancelRequest
- PromotionRecallStartRequest / PromotionRecallResultRequest
- PromotionReviewRequest
- PromotionUpdateLikeRequest
- PromotionResponse / PromotionPage
- PromotionDuplicateWarning
- PromotionListFilters
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
    field_validator,
    model_validator,
)

from app.modules.promotion.enums import (
    PublishStatus,
    RecallStatus,
    ReviewAction,
    SettlementStatus,
)


_QuoteField = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=10, decimal_places=2),
]


# ---------------------------------------------------------------------------
# 通用基类 / Create / Update
# ---------------------------------------------------------------------------


class PromotionBase(BaseModel):
    """共享字段（创建 + 编辑）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    style_id: UUID
    sku_id: UUID | None = None
    blogger_id: UUID
    platform: str = Field(min_length=1, max_length=16)
    cooperation_date: date
    scheduled_publish_date: date | None = None
    quote_amount: _QuoteField | None = None
    """创建时若为 None 则从 blogger.quote 快照；后续编辑可修改。"""
    note_title: str | None = Field(default=None, max_length=255)
    remark: str | None = None


class PromotionCreate(PromotionBase):
    """创建入参。"""

    pass


class PromotionUpdate(BaseModel):
    """部分更新（PATCH 语义）。

    禁止修改：style_id / blogger_id / cooperation_date / 三个状态字段（走专门接口）。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    sku_id: UUID | None = None
    platform: str | None = Field(default=None, min_length=1, max_length=16)
    scheduled_publish_date: date | None = None
    quote_amount: _QuoteField | None = None
    note_title: str | None = Field(default=None, max_length=255)
    like_count: int | None = Field(default=None, ge=0)
    remark: str | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# 状态推进入参
# ---------------------------------------------------------------------------


class PromotionPublishRequest(BaseModel):
    """publish 入参（BR-U04-20）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    publish_url: str = Field(min_length=1, max_length=512)
    actual_publish_date: date

    @field_validator("publish_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("publish_url 必须以 http:// 或 https:// 开头")
        return v


class PromotionCancelRequest(BaseModel):
    """cancel 入参（BR-U04-20）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    cancel_reason: str = Field(min_length=1, max_length=2000)


class PromotionMarkAbnormalRequest(BaseModel):
    """mark_abnormal 入参。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    remark: str = Field(min_length=1, max_length=2000)


class PromotionRecallStartRequest(BaseModel):
    """start_recall 入参（BR-U04-21）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    recall_reason: str | None = Field(default=None, max_length=2000)


class PromotionRecallResultRequest(BaseModel):
    """recall_success / recall_failure 入参。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    remark: str | None = Field(default=None, max_length=2000)


class PromotionReviewRequest(BaseModel):
    """审核入参（BR-U04-22 + EP05-S13）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    action: ReviewAction
    review_reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _require_reason_on_reject(self) -> "PromotionReviewRequest":
        if self.action == ReviewAction.REJECT and not self.review_reason:
            raise ValueError("驳回时 review_reason 必填")
        return self


class PromotionUpdateLikeRequest(BaseModel):
    """采集 Worker 调用：更新 like_count（U13 内部 API）。"""

    model_config = ConfigDict()

    like_count: int = Field(ge=0)


# ---------------------------------------------------------------------------
# 响应 / 列表 / 重复警告
# ---------------------------------------------------------------------------


class PromotionDuplicateWarning(BaseModel):
    """同款 + 同博主存在 active 推广（EP05-S04 warning，非阻塞）。"""

    model_config = ConfigDict(from_attributes=True)

    promotion_id: UUID
    internal_code: str
    publish_status: str
    cooperation_date: date


class PromotionResponse(BaseModel):
    """推广响应。

    敏感字段（quote_amount / cost_snapshot / cpl）按角色过滤
    （详见 ``service.PromotionService.to_response``）。
    衍生字段（urge_status / dual_platform / effective_like_count / is_hit / cpl）
    由 service 层实时计算填入。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    internal_code: str
    style_id: UUID
    sku_id: UUID | None = None
    blogger_id: UUID
    pr_id: UUID | None = None

    # 快照字段
    style_code_snapshot: str
    style_short_name_snapshot: str
    quote_amount: Decimal | None = None  # 敏感
    cost_snapshot: Decimal | None = None  # 敏感

    # 业务字段
    platform: str
    cooperation_date: date
    scheduled_publish_date: date | None = None
    actual_publish_date: date | None = None
    publish_url: str | None = None
    cancel_reason: str | None = None
    recall_reason: str | None = None
    like_count: int | None = None
    note_title: str | None = None
    remark: str | None = None

    # 状态字段
    publish_status: str
    recall_status: str
    settlement_status: str

    # 审核
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    review_action: str | None = None
    review_reason: str | None = None

    # 通用
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # 衍生字段（service 实时填入；不持久化）
    urge_status: str | None = None
    dual_platform: bool = False
    effective_like_count: int | None = None
    is_hit: bool = False
    cpl: Decimal | None = None  # 敏感

    # 重复警告（仅 create / detail 视图填入）
    duplicate_warnings: list[PromotionDuplicateWarning] = Field(
        default_factory=list
    )


class PromotionPage(BaseModel):
    items: list[PromotionResponse]
    total: int
    page: int
    page_size: int


# ---------------------------------------------------------------------------
# 列表 filter
# ---------------------------------------------------------------------------


class PromotionListFilters(BaseModel):
    """列表过滤入参（query string 解析后构造）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    keyword: str | None = Field(default=None, max_length=64)
    publish_status: PublishStatus | None = None
    recall_status: RecallStatus | None = None
    settlement_status: SettlementStatus | None = None
    platform: str | None = Field(default=None, max_length=16)
    blogger_id: UUID | None = None
    style_id: UUID | None = None
    pr_id: UUID | None = None
    cooperation_date_from: date | None = None
    cooperation_date_to: date | None = None
    scheduled_publish_date_from: date | None = None
    scheduled_publish_date_to: date | None = None
    is_active: bool | None = True
    only_dual_platform: bool = False
    is_hit: bool | None = None


__all__ = [
    "PromotionBase",
    "PromotionCancelRequest",
    "PromotionCreate",
    "PromotionDuplicateWarning",
    "PromotionListFilters",
    "PromotionMarkAbnormalRequest",
    "PromotionPage",
    "PromotionPublishRequest",
    "PromotionRecallResultRequest",
    "PromotionRecallStartRequest",
    "PromotionResponse",
    "PromotionReviewRequest",
    "PromotionUpdate",
    "PromotionUpdateLikeRequest",
]
