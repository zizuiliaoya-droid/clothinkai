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


class PublishUrlRequiredError(ValidationError):
    """publish 时 publish_url 必填。"""

    code = "PUBLISH_URL_REQUIRED"


class CancelReasonRequiredError(ValidationError):
    """cancel 时 cancel_reason 必填。"""

    code = "CANCEL_REASON_REQUIRED"


class ReviewReasonRequiredError(ValidationError):
    """review reject 时 review_reason 必填。"""

    code = "REVIEW_REASON_REQUIRED"


class SelfReviewForbiddenError(ValidationError):
    """禁止自审（reviewed_by != pr_id）。"""

    code = "SELF_REVIEW_FORBIDDEN"
    status_code = 403


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
    "CancelReasonRequiredError",
    "FieldPermissionDenied",  # re-exported from modules/product/exceptions
    "InvalidBloggerReferenceError",
    "InvalidSkuReferenceError",
    "InvalidStyleReferenceError",
    "PromotionInternalCodeConflictError",
    "PromotionNotFoundError",
    "PublishUrlRequiredError",
    "ReviewReasonRequiredError",
    "SelfReviewForbiddenError",
    "SequenceOverflowError",
    "StateTransitionConflictError",
]
