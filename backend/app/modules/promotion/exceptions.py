"""U04 promotion 模块业务异常。

继承自 ``core/exceptions.py`` 的 base 异常。
按 nfr-design/logical-components.md §1.3 决策：
``FieldPermissionDenied`` 直接复用 ``modules/product/exceptions``，不重复定义。
U09 字段级权限落地后统一移到 ``core/exceptions.py``。
"""

from __future__ import annotations

from app.core.exceptions import (
    AppException,
    DuplicateResourceError,
    IllegalStateTransitionError,
    ResourceNotFoundError,
    ValidationError,
)

# Re-export 复用的字段权限异常
from app.modules.product.exceptions import FieldPermissionDenied

# ---------------------------------------------------------------------------
# 资源未找到
# ---------------------------------------------------------------------------


class PromotionNotFoundError(ResourceNotFoundError):
    code = "PROMOTION_NOT_FOUND"


# ---------------------------------------------------------------------------
# 唯一约束冲突
# ---------------------------------------------------------------------------


class PromotionInternalCodeConflictError(DuplicateResourceError):
    """internal_code 冲突。理论上不应发生（序列号原子分配），仅作为兜底兜底。"""

    code = "PROMOTION_INTERNAL_CODE_CONFLICT"
    status_code = 409


class SequenceOverflowError(AppException):
    """单日序列号超过 9999（极端情况，需扩位或人工分流）。"""

    code = "PROMOTION_SEQUENCE_OVERFLOW"
    status_code = 500


# ---------------------------------------------------------------------------
# 业务校验
# ---------------------------------------------------------------------------


class InvalidStyleReferenceError(ValidationError):
    """style_id 不存在 / 已软删（创建时校验）。"""

    code = "INVALID_STYLE_REFERENCE"


class InvalidSkuReferenceError(ValidationError):
    """sku_id 不存在 / 已软删 / 不属于 style_id（创建时校验）。"""

    code = "INVALID_SKU_REFERENCE"


class InvalidBloggerReferenceError(ValidationError):
    """blogger_id 不存在 / 已软删（创建时校验）。"""

    code = "INVALID_BLOGGER_REFERENCE"


class InvalidGoodsReferenceError(ValidationError):
    """goods_main_id 不存在 / 不包含该款式（创建与编辑时校验）。"""

    code = "INVALID_GOODS_REFERENCE"


class InvalidPaymentQrAttachmentError(ValidationError):
    """收款码附件不存在、跨租户或属性不符合要求。"""

    code = "INVALID_PAYMENT_QR_ATTACHMENT"


class PublishUrlRequiredError(ValidationError):
    """publish 时 publish_url 必填。"""

    code = "PUBLISH_URL_REQUIRED"


class CancelReasonRequiredError(ValidationError):
    """cancel 时 cancel_reason 必填。"""

    code = "CANCEL_REASON_REQUIRED"


class ReviewReasonRequiredError(ValidationError):
    """review reject 时 review_reason 必填。"""

    code = "REVIEW_REASON_REQUIRED"


class CooperationModeImmutableError(ValidationError):
    """单据已有合作模式后不允许修改（PRD V1.4 模块二硬约束）。

    改模式等于改成本口径（寄拍样品成本恒 0、置换服务费恒 0），已定稿的单据改了
    会让历史报表与结款金额对不上。历史数据的空值允许补一次，补完即锁。
    """

    code = "COOPERATION_MODE_IMMUTABLE"
    status_code = 409


class CooperationModeRequiredError(ValidationError):
    """需要合作模式才能继续（历史数据补齐场景）。"""

    code = "COOPERATION_MODE_REQUIRED"


class ReturnWaybillRequiredError(ValidationError):
    """寄拍模式审核通过前必须上传博主寄回衣服单号。

    PRD 模块二硬约束：没有单号单据不流转到待财务付款，财务看不到、不能结款。
    后端拦截，不能只靠前端控制。
    """

    code = "RETURN_WAYBILL_REQUIRED"


class RejectReasonCategoryRequiredError(ValidationError):
    """驳回时原因分类必填（PRD 改动 5：三选一）。"""

    code = "REJECT_REASON_CATEGORY_REQUIRED"


class SelfReviewForbiddenError(ValidationError):
    """禁止自审（reviewed_by != pr_id）。"""

    code = "SELF_REVIEW_FORBIDDEN"
    status_code = 403


# ---------------------------------------------------------------------------
# 复盘（PRD V1.4 改动 4）
# ---------------------------------------------------------------------------


class SettlementNotPaidError(ValidationError):
    """录 7 天数据要求结款已完成。

    复盘是结完款之后的事。钱还没结清就复盘没有意义 —— ROI 的分母都还没定。
    """

    code = "SETTLEMENT_NOT_PAID"
    status_code = 409


class MetricsScreenshotRequiredError(ValidationError):
    """7 天数据必须带截图。

    PRD 原文「发布满 7 天，PR 录入点赞/收藏/评论 + 截图」。数字可以手填错，
    截图是对账依据。
    """

    code = "METRICS_SCREENSHOT_REQUIRED"


class RetroSelfConfirmForbiddenError(ValidationError):
    """禁止确认自己写的复盘。

    和自审禁止同一道理：自己写自己批，主管这道关就没有意义。

    这条必须在 service 层挡 —— ``promotion.retro:confirm`` 的一级域是 promotion，
    PR 持 ``promotion.*:*`` 会被通配命中，权限层拦不住。
    """

    code = "RETRO_SELF_CONFIRM_FORBIDDEN"
    status_code = 403


class BrandCommentScreenshotRequiredError(ValidationError):
    """提交发布审核前必须上传品牌词评论截图。

    PRD 改动 5（业务方明确保留不变）：「品牌词评论截图（PR 提交发布审核时必传）」。
    「提交发布审核」就是 ``publish`` —— PR 把单据交给主管核查的那一步。

    这是会挡业务的硬约束：没有截图发不了单。不加 DB CHECK 是因为生产有 5154 条未发布的
    历史单，约束会把它们全卡住；门槛只在 ``publish()`` 里。
    """

    code = "BRAND_COMMENT_SCREENSHOT_REQUIRED"


class RetroContentMissingError(ValidationError):
    """确认复盘时找不到复盘文字。

    正常流程走不到这里（``submit_retro`` 才能进待确认）。真出现说明子表记录被清掉了，
    不该让主管确认一条空复盘。
    """

    code = "RETRO_CONTENT_MISSING"


# ---------------------------------------------------------------------------
# 状态机冲突
# ---------------------------------------------------------------------------


class StateTransitionConflictError(IllegalStateTransitionError):
    """乐观并发冲突：UPDATE WHERE old_state 影响 0 行（FB7）。

    与 IllegalStateTransitionError 的区别：
    - IllegalStateTransitionError = 业务前置校验失败（确定不能转移）
    - StateTransitionConflictError = 并发竞争 / 软删 / 跨租户被拒
    """

    code = "PROMOTION_STATE_CONFLICT"
    status_code = 409


# ---------------------------------------------------------------------------
# 重复检测（warning，非阻塞）
# ---------------------------------------------------------------------------


class ActiveDuplicatePromotionWarning(ValidationError):
    """同款 + 同博主存在 active 推广（EP05-S04 warning，非阻塞）。

    service 层捕获并以 warnings 形式返回，不直接抛给前端。
    """

    code = "ACTIVE_DUPLICATE_PROMOTION"


__all__ = [
    "ActiveDuplicatePromotionWarning",
    "BrandCommentScreenshotRequiredError",
    "CancelReasonRequiredError",
    "CooperationModeImmutableError",
    "CooperationModeRequiredError",
    "FieldPermissionDenied",  # re-exported from modules/product/exceptions
    "RejectReasonCategoryRequiredError",
    "ReturnWaybillRequiredError",
    "InvalidBloggerReferenceError",
    "InvalidPaymentQrAttachmentError",
    "InvalidSkuReferenceError",
    "InvalidStyleReferenceError",
    "MetricsScreenshotRequiredError",
    "PromotionInternalCodeConflictError",
    "PromotionNotFoundError",
    "PublishUrlRequiredError",
    "RetroContentMissingError",
    "RetroSelfConfirmForbiddenError",
    "ReviewReasonRequiredError",
    "SelfReviewForbiddenError",
    "SequenceOverflowError",
    "SettlementNotPaidError",
    "StateTransitionConflictError",
]
