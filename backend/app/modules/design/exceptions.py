"""U10a design 模块业务异常（复用 core base）。"""

from __future__ import annotations

from app.core.exceptions import (
    AppException,
    ResourceNotFoundError,
    ValidationError,
)


class StyleNotFoundError(ResourceNotFoundError):
    code = "STYLE_NOT_FOUND"


class DesignStateConflictError(AppException):
    """状态推进乐观并发冲突（design_status 已被并发修改）。"""

    code = "DESIGN_STATE_CONFLICT"
    status_code = 409
    message = "款式状态已变更，请刷新后重试"


class RejectReasonRequiredError(ValidationError):
    code = "REJECT_REASON_REQUIRED"


class CancelReasonRequiredError(ValidationError):
    code = "CANCEL_REASON_REQUIRED"


class InvalidCostBreakdownError(ValidationError):
    code = "INVALID_COST_BREAKDOWN"


__all__ = [
    "CancelReasonRequiredError",
    "DesignStateConflictError",
    "InvalidCostBreakdownError",
    "RejectReasonRequiredError",
    "StyleNotFoundError",
]
