"""谈款审核业务异常。"""

from __future__ import annotations

from app.core.exceptions import (
    AppException,
    IllegalStateTransitionError,
    ResourceNotFoundError,
    ValidationError,
)


class NegotiationNotFoundError(ResourceNotFoundError):
    code = "NEGOTIATION_NOT_FOUND"


class NegotiationStateConflictError(IllegalStateTransitionError):
    """条件 UPDATE 影响 0 行：别人刚处理过（流程线 7.1，409）。

    状态机不允许仍是父类的 422 ``ILLEGAL_STATE_TRANSITION``；测试比 ``exc.code``，不只比异常类型。
    """

    code = "NEGOTIATION_STATE_CONFLICT"
    status_code = 409
    message = "谈款单刚被别人处理过，请刷新后再试"


class NegotiationSelfReviewForbiddenError(AppException):
    """禁止审核自己提交的谈款单。

    与推广单的自审禁止同一道理：自己谈的款自己批，审核这道关就没有意义了。
    """

    code = "NEGOTIATION_SELF_REVIEW_FORBIDDEN"
    status_code = 403


class InvalidNegotiationBloggerError(ValidationError):
    code = "INVALID_NEGOTIATION_BLOGGER"


class InvalidNegotiationStyleError(ValidationError):
    code = "INVALID_NEGOTIATION_STYLE"


class InvalidNegotiationGoodsError(ValidationError):
    code = "INVALID_NEGOTIATION_GOODS"


__all__ = [
    "InvalidNegotiationBloggerError",
    "InvalidNegotiationGoodsError",
    "InvalidNegotiationStyleError",
    "NegotiationNotFoundError",
    "NegotiationSelfReviewForbiddenError",
    "NegotiationStateConflictError",
]
