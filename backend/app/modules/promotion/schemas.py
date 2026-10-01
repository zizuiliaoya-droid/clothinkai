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
from typing import Annotated
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.modules.promotion.enums import (
    CooperationMode,
    PublishStatus,
    RecallStatus,
    RejectReasonCategory,
    ReviewAction,
    SettlementStatus,
)

_QuoteField = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=10, decimal_places=2),
]

_FeeField = Annotated[
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
    goods_main_id: UUID | None = None
    """这次推广归属的商品（套装或单品）。

    不传就由服务端取该款式的主商品（非套装优先），保持与导入及旧客户端的兼容。
    款式既单卖又进套装时前端会要求显式选择 —— 那种情况系统猜不准。
    """
    blogger_id: UUID
    platform: str = Field(min_length=1, max_length=16)
    cooperation_date: date | None = None
    """合作日期。

    HTTP 建单时**忽略此字段**，服务端一律取建单当天（PRD 改动 5：自动生成、不可改）。
    保留它只为兼容旧客户端的请求体，不报错但也不生效。Excel 导入走另一条路径，
    那里仍然按文件里的日期落库 —— 不然历史数据导不进来。
    """

    scheduled_publish_date: date | None = None
    quote_amount: _QuoteField | None = None
    """创建时若为 None 则从 blogger.quote 快照；后续编辑可修改。

    置换模式下服务端强制为 0（PRD：置换无博主服务费），前端传什么都会被覆盖。
    """
    note_title: str | None = Field(default=None, max_length=255)
    remark: str | None = None
    # 人工源列扩展（颜色及规格/打单地址/发货单号/订单号/寄回单号/合作形式/收藏数/评论数/博主风格 等）
    # 注意：「合作方式」已提成 typed 字段 cooperation_mode，不再从这里走。
    source_extra: dict = Field(default_factory=dict)


class PromotionCreate(PromotionBase):
    """创建入参。"""

    cooperation_mode: CooperationMode
    """寄拍 / 送拍 / 置换。新建必填 —— 它决定成本怎么算、审核通过后走哪个出口，
    缺了它后面每一步都没法判断。历史数据允许为空，但新单不允许。"""

    return_shipping_fee: _FeeField | None = None
    """寄回运费。一般在召回时才录，建单时通常为空。"""


class PromotionUpdate(BaseModel):
    """部分更新（PATCH 语义）。

    禁止修改：style_id / blogger_id / cooperation_date / 三个状态字段（走专门接口）。

    ``goods_main_id`` 可改：归属录错、或者套装是推广录完之后才建的，都要能修正。
    改动会即时反映到投产报表的推广费归属上。

    ``cooperation_mode`` 只能从空补一次，有值后 service 层拒绝修改（PRD：单据生成后
    不可修改合作模式）。放在这里是为了让历史数据能补齐，不是为了允许改。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    sku_id: UUID | None = None
    goods_main_id: UUID | None = None
    cooperation_mode: CooperationMode | None = None
    return_shipping_fee: _FeeField | None = None
    platform: str | None = Field(default=None, min_length=1, max_length=16)
    scheduled_publish_date: date | None = None
    quote_amount: _QuoteField | None = None
    note_title: str | None = Field(default=None, max_length=255)
    like_count: int | None = Field(default=None, ge=0)
    remark: str | None = None
    is_active: bool | None = None
    source_extra: dict | None = None


class PromotionPaymentQrUploadInitRequest(BaseModel):
    """收款码上传初始化；bucket/purpose 由服务端强制指定。"""

    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    filename: str | None = Field(default=None, max_length=255)
    mime_type: str = Field(min_length=1, max_length=64)
    size_bytes: int = Field(ge=1, le=10 * 1024 * 1024)


class PromotionPaymentQrUploadInitResponse(BaseModel):
    attachment_id: UUID
    presigned_url: str
    expires_in_seconds: int = 900


class PromotionPaymentQrBindRequest(BaseModel):
    payment_qr_attachment_id: UUID


class PromotionWarehouseWaybillRequest(BaseModel):
    waybill: str = Field(min_length=1, max_length=128)


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
    review_reason_category: RejectReasonCategory | None = None
    """驳回原因分类，驳回时必填（PRD 改动 5 三选一）。审核通过时忽略。"""

    @model_validator(mode="after")
    def _require_reason_on_reject(self) -> PromotionReviewRequest:
        if self.action == ReviewAction.REJECT:
            if not self.review_reason:
                raise ValueError("驳回时 review_reason 必填")
            if self.review_reason_category is None:
                raise ValueError("驳回时 review_reason_category 必填（三选一）")
        return self


class PromotionReturnWaybillRequest(BaseModel):
    """上传博主寄回衣服单号。

    寄拍模式审核通过后用这个接口补单号，补完才能流转到待财务付款。
    与仓库发货单号（``PromotionWarehouseWaybillRequest``）是两个方向：
    那个是寄给博主，这个是博主寄回来。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    return_waybill: str = Field(min_length=1, max_length=128)


class PromotionUpdateLikeRequest(BaseModel):
    """采集 Worker 调用：更新 like_count（U13 内部 API）。"""

    model_config = ConfigDict()

    like_count: int = Field(ge=0)


# ---------------------------------------------------------------------------
# 复盘（PRD V1.4 改动 4）
# ---------------------------------------------------------------------------


class PromotionMetricsRequest(BaseModel):
    """发布满 7 天的数据录入。

    PRD 原文「发布满 7 天，PR 录入点赞/收藏/评论 + 截图」，三个指标都必填。
    截图走 multipart 端点单独传，不在这个 body 里。
    """

    model_config = ConfigDict()

    like_count: int = Field(ge=0)
    collect_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)


class RetrospectiveSubmitRequest(BaseModel):
    """PR 提交复盘文字。

    PRD：自由描述（数据表现、博主配合度、是否二搭、下次合作建议），不拆结构化字段。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=5000)


class RetrospectiveConfirmRequest(BaseModel):
    """主管确认或打回复盘。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    approve: bool = True
    opinion: str | None = Field(default=None, max_length=2000)
    """打回时的意见。PR 得知道要改什么。"""

    @model_validator(mode="after")
    def _require_opinion_on_reject(self) -> RetrospectiveConfirmRequest:
        if not self.approve and not self.opinion:
            raise ValueError("打回复盘时必须写明意见")
        return self


class PromotionAmountLogResponse(BaseModel):
    """金额变更时间线的一条（PRD 第 10 节第 14 条）。

    读取受字段级权限门控（``field.promotion.quote_amount:read``），不是靠 scope ——
    运营持 ``promotion.*:read``，新建任何 ``promotion.xxx:read`` 都会被通配命中。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    field_name: str
    before_value: Decimal | None = None
    after_value: Decimal | None = None
    change_source: str
    """手动编辑 / 模式初始化 / 模式兜底 —— 回答「这个 0 是我改的还是系统压的」。"""

    changed_by: UUID | None = None
    changed_by_name: str | None = None
    created_at: datetime


class RetrospectiveResponse(BaseModel):
    """一条复盘记录。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    blogger_id: UUID
    promotion_id: UUID
    promotion_internal_code: str | None = None
    style_code: str | None = None
    content: str
    created_by: UUID | None = None
    created_by_name: str | None = None
    confirmed_by: UUID | None = None
    confirmed_by_name: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime


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
    goods_main_id: UUID | None = None
    blogger_id: UUID
    pr_id: UUID | None = None

    # 快照字段
    style_code_snapshot: str
    style_short_name_snapshot: str
    style_main_image_url: str | None = None
    # 商品归属实时取，不做快照 —— 归属可改，快照会过期
    goods_code: str | None = None
    goods_is_suit: bool = False
    quote_amount: Decimal | None = None  # 敏感
    cost_snapshot: Decimal | None = None  # 敏感

    # 合作模式与成本（PRD V1.4 模块二）
    cooperation_mode: str | None = None
    return_shipping_fee: Decimal | None = None  # 敏感
    total_promo_cost: Decimal | None = None  # 敏感
    """站外推广成本 = 博主服务费 + 样品成本 + 寄回运费。数据库生成列，与 quote_amount
    同样受读权限门控 —— 它是三项金额之和，能看到它等于能推算出金额。"""

    return_waybill: str | None = None
    """博主寄回衣服单号。寄拍模式没有它就不能流转到待财务付款。"""

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

    # 发布满 7 天的数据（PRD V1.4 改动 4）
    collect_count: int | None = None
    comment_count: int | None = None
    metrics_recorded_at: datetime | None = None
    metrics_signed_url: str | None = None
    """7 天数据截图的签名 URL，现签不落库。"""

    brand_comment_attachment_id: UUID | None = None
    brand_comment_signed_url: str | None = None
    """品牌词评论截图。PRD 改动 5：没有它 publish 会 422。

    attachment_id 也返回，这样前端不用靠 URL 是否为空来判断「传过没有」——
    签名失败（R2 抖动）时 URL 为空但截图其实在。
    """

    # 状态字段
    publish_status: str
    recall_status: str
    settlement_status: str
    retro_status: str = "未开始"
    """未开始 / 待复盘 / 待确认 / 已完成（PRD V1.4 改动 4，第 4 个并行状态机）。"""

    retro_confirmed_by: UUID | None = None
    retro_confirmed_at: datetime | None = None
    retro_content: str | None = None
    """当前生效的复盘文字 = 本单最新的那条。被打回重写时旧版留在子表里。"""

    # 审核
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
    review_action: str | None = None
    review_reason: str | None = None
    review_reason_category: str | None = None

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

    # 人工源列扩展（对齐 final.xlsx 站外推广源列）
    source_extra: dict = Field(default_factory=dict)

    # 结款附件（仅 PR/PR主管/管理员可见；warehouse 始终为 null）
    payment_qr_attachment_id: UUID | None = None
    payment_qr_signed_url: str | None = None
    settlement_payment_proof_signed_url: str | None = None

    # 重复警告（仅 create / detail 视图填入）
    duplicate_warnings: list[PromotionDuplicateWarning] = Field(default_factory=list)


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
    has_print_address: bool | None = None
    has_waybill: bool | None = None


__all__ = [
    "PromotionBase",
    "PromotionCancelRequest",
    "PromotionCreate",
    "PromotionAmountLogResponse",
    "PromotionDuplicateWarning",
    "PromotionListFilters",
    "PromotionMarkAbnormalRequest",
    "PromotionMetricsRequest",
    "PromotionPage",
    "PromotionPaymentQrBindRequest",
    "PromotionPaymentQrUploadInitRequest",
    "PromotionPaymentQrUploadInitResponse",
    "PromotionPublishRequest",
    "PromotionRecallResultRequest",
    "PromotionRecallStartRequest",
    "PromotionResponse",
    "PromotionReviewRequest",
    "PromotionUpdate",
    "PromotionUpdateLikeRequest",
    "PromotionWarehouseWaybillRequest",
    "RetrospectiveConfirmRequest",
    "RetrospectiveResponse",
    "RetrospectiveSubmitRequest",
]
