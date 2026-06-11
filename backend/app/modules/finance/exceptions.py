"""U05 finance 模块业务异常（18 个）。

继承自 ``core/exceptions.py`` 的 base 异常。
``FieldPermissionDenied`` 复用自 ``modules/product/exceptions``（与 U03/U04 同模式）。
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


class SettlementNotFoundError(ResourceNotFoundError):
    code = "SETTLEMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 唯一约束冲突
# ---------------------------------------------------------------------------


class SettlementNoConflictError(DuplicateResourceError):
    """settlement_no 冲突。理论上不应发生（序列号原子分配 + 永久 UNIQUE）。"""

    code = "SETTLEMENT_NO_CONFLICT"
    status_code = 409


class SequenceOverflowError(AppException):
    """单日序列号超过 9999（极端情况，需扩位或人工分流）。"""

    code = "SETTLEMENT_SEQUENCE_OVERFLOW"
    status_code = 500


# ---------------------------------------------------------------------------
# 状态机冲突
# ---------------------------------------------------------------------------


class StateTransitionConflictError(IllegalStateTransitionError):
    """乐观并发冲突：UPDATE WHERE old_state 影响 0 行（FB7）。

    与 IllegalStateTransitionError 的区别：
    - IllegalStateTransitionError = 业务前置校验失败
    - StateTransitionConflictError = 并发竞争 / 跨租户被拒
    """

    code = "SETTLEMENT_STATE_CONFLICT"
    status_code = 409


class SelfReviewForbiddenError(ValidationError):
    """禁止自审（reviewer != promotion.pr_id，与 U04 一致）。"""

    code = "SELF_REVIEW_FORBIDDEN"
    status_code = 403


# ---------------------------------------------------------------------------
# 业务校验
# ---------------------------------------------------------------------------


class ReviewReasonRequiredError(ValidationError):
    """reject 时 review_reason 必填。"""

    code = "REVIEW_REASON_REQUIRED"


class PaymentAmountRequiredError(ValidationError):
    """fill_payment 时 payment_amount 必填且 > 0。"""

    code = "PAYMENT_AMOUNT_REQUIRED"


class PaymentFieldMissingError(ValidationError):
    """mark_paid 时 payment_date / attachment_id 任一缺失。"""

    code = "PAYMENT_FIELD_MISSING"


class ExtraItemNotAllowedError(ValidationError):
    """非 PENDING_PAYMENT 状态不允许增加 / 修改 extra_item。"""

    code = "EXTRA_ITEM_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Attachment 6 项强校验异常（FB4）
# ---------------------------------------------------------------------------


class InvalidAttachmentReferenceError(ValidationError):
    """attachment 不存在 / 跨租户。

    跨租户场景**不暴露** attachment 是否存在（统一 422，避免侧信道）。
    """

    code = "INVALID_ATTACHMENT_REFERENCE"


class InvalidAttachmentBucketError(ValidationError):
    """attachment.bucket != 'private'。"""

    code = "INVALID_ATTACHMENT_BUCKET"


class InvalidAttachmentPurposeError(ValidationError):
    """attachment.purpose != 'settlement_proof'。"""

    code = "INVALID_ATTACHMENT_PURPOSE"


class InvalidAttachmentMimeError(ValidationError):
    """attachment.mime_type 不在白名单（image/jpeg / png / webp / pdf）。"""

    code = "INVALID_ATTACHMENT_MIME"


class AttachmentTooLargeError(ValidationError):
    """attachment.size_bytes > 10MB。"""

    code = "ATTACHMENT_TOO_LARGE"


class AttachmentNotReadyError(ValidationError):
    """attachment.status != 'ready'。"""

    code = "ATTACHMENT_NOT_READY"


# ---------------------------------------------------------------------------
# U16 拍单 / 刷单 / 余额
# ---------------------------------------------------------------------------


class AmountExpressionInvalidError(ValidationError):
    """金额表达式非法（非"数字"或"数字-数字"，或结果为负）。"""

    code = "AMOUNT_EXPRESSION_INVALID"


class BalanceMismatchError(ValidationError):
    """余额计算值与人工填写 expected_balance 不一致。"""

    code = "BALANCE_MISMATCH"


class BalanceTypeFieldMismatchError(ValidationError):
    """record_type 与 income/expense 字段错配。"""

    code = "BALANCE_TYPE_FIELD_MISMATCH"


__all__ = [
    "AmountExpressionInvalidError",
    "AttachmentNotReadyError",
    "AttachmentTooLargeError",
    "BalanceMismatchError",
    "BalanceTypeFieldMismatchError",
    "ExtraItemNotAllowedError",
    "FieldPermissionDenied",  # re-exported from modules/product/exceptions
    "InvalidAttachmentBucketError",
    "InvalidAttachmentMimeError",
    "InvalidAttachmentPurposeError",
    "InvalidAttachmentReferenceError",
    "PaymentAmountRequiredError",
    "PaymentFieldMissingError",
    "ReviewReasonRequiredError",
    "SelfReviewForbiddenError",
    "SequenceOverflowError",
    "SettlementNoConflictError",
    "SettlementNotFoundError",
    "StateTransitionConflictError",
]
