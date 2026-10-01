"""谈款审核业务异常。"""

from __future__ import annotations

from app.core.exceptions import (
    AppException,
    ResourceNotFoundError,
    ValidationError,
)


class NegotiationNotFoundError(ResourceNotFoundError):
    code = "NEGOTIATION_NOT_FOUND"


class NegotiationNotEditableError(AppException):
    """只有草稿和被驳回的单据能改。

    提交审核后 PR 不该再动内容（否则主管看到的和审的不是一回事）；
    审核通过后推广单已经建出来了，改谈款信息也同步不过去。
    """

    code = "NEGOTIATION_NOT_EDITABLE"
    status_code = 409


class NegotiationNotSubmittableError(AppException):
    """只有草稿和被驳回的单据能提交审核。"""

    code = "NEGOTIATION_NOT_SUBMITTABLE"
    status_code = 409


class NegotiationNotReviewableError(AppException):
    """只有待审核的单据能审。"""

    code = "NEGOTIATION_NOT_REVIEWABLE"
    status_code = 409


class NegotiationSelfReviewForbiddenError(AppException):
    """禁止审核自己提交的谈款单。

    与推广单的自审禁止同一道理：自己谈的款自己批，审核这道关就没有意义了。
    """

    code = "NEGOTIATION_SELF_REVIEW_FORBIDDEN"
    status_code = 403


class NegotiationReviewOpinionRequiredError(ValidationError):
    """驳回时审核意见必填 —— PR 得知道要改什么。"""

    code = "NEGOTIATION_REVIEW_OPINION_REQUIRED"


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
    "NegotiationNotEditableError",
    "NegotiationNotFoundError",
    "NegotiationNotReviewableError",
    "NegotiationNotSubmittableError",
    "NegotiationReviewOpinionRequiredError",
    "NegotiationSelfReviewForbiddenError",
]
