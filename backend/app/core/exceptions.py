"""统一异常体系。

所有应用层异常继承 AppException，被 core/errors.py 的处理器转为 JSON 响应。
"""

from __future__ import annotations

from typing import Any


class AppException(Exception):
    """应用基类异常。"""

    code: str = "APP_ERROR"
    status_code: int = 500
    message: str = "Internal application error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# 认证 / 授权
# ---------------------------------------------------------------------------
class InvalidCredentialsError(AppException):
    code = "INVALID_CREDENTIALS"
    status_code = 401
    message = "用户名或密码错误"


class TokenInvalidError(AppException):
    code = "TOKEN_INVALID"
    status_code = 401
    message = "Token 无效或已过期"


class TokenExpiredError(AppException):
    code = "TOKEN_EXPIRED"
    status_code = 401
    message = "Token 已过期，请重新登录"


class AccountDisabledError(AppException):
    code = "ACCOUNT_DISABLED"
    status_code = 401
    message = "账号已被禁用"


class AccountLockedError(AppException):
    code = "ACCOUNT_LOCKED"
    status_code = 423
    message = "账号已被锁定，请联系管理员解锁"


class PasswordMustChangeError(AppException):
    code = "PASSWORD_MUST_CHANGE"
    status_code = 423
    message = "首次登录或密码已重置，请先修改密码"


class PermissionDeniedError(AppException):
    code = "PERMISSION_DENIED"
    status_code = 403
    message = "权限不足"


class FieldPermissionDenied(PermissionDeniedError):
    """字段级写权限拒绝（U09 统一字段级权限）。

    兼容签名：``FieldPermissionDenied(field=...)``（U02-U05 既有调用）与
    ``FieldPermissionDenied(field=..., entity=...)``（U09 新调用）。
    """

    code = "FIELD_PERMISSION_DENIED"

    def __init__(self, field: str, entity: str | None = None) -> None:
        details: dict[str, Any] = {"field": field}
        if entity is not None:
            details["entity"] = entity
        super().__init__(f"无权写入字段: {field}", details=details)
        self.field = field
        self.entity = entity


# ---------------------------------------------------------------------------
# 多租户
# ---------------------------------------------------------------------------
class TenantContextMissingError(AppException):
    code = "TENANT_CONTEXT_MISSING"
    status_code = 500
    message = "缺失 tenant_id 上下文（系统任务必须显式使用 system_context）"


class TenantContextMismatchError(AppException):
    code = "TENANT_CONTEXT_MISMATCH"
    status_code = 500
    message = "对象 tenant_id 与上下文不匹配"


# ---------------------------------------------------------------------------
# 限流
# ---------------------------------------------------------------------------
class RateLimitedError(AppException):
    code = "RATE_LIMITED"
    status_code = 429
    message = "请求过于频繁，请稍后重试"

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if retry_after_seconds is not None:
            details["retry_after_seconds"] = retry_after_seconds
        super().__init__(message, details=details)


# ---------------------------------------------------------------------------
# 资源
# ---------------------------------------------------------------------------
class ResourceNotFoundError(AppException):
    code = "RESOURCE_NOT_FOUND"
    status_code = 404
    message = "资源不存在"


class DuplicateResourceError(AppException):
    code = "DUPLICATE_RESOURCE"
    status_code = 409
    message = "资源已存在"


class ValidationError(AppException):
    code = "VALIDATION_ERROR"
    status_code = 422
    message = "请求数据校验失败"


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------
class IllegalStateTransitionError(AppException):
    code = "ILLEGAL_STATE_TRANSITION"
    status_code = 422
    message = "当前状态不允许此操作"


# ---------------------------------------------------------------------------
# 附件
# ---------------------------------------------------------------------------
class AttachmentError(AppException):
    code = "ATTACHMENT_ERROR"
    status_code = 500
    message = "附件操作失败"


# ---------------------------------------------------------------------------
# 事件总线（U04 引入）
# ---------------------------------------------------------------------------
class MissingRequiredHandlerError(AppException):
    """强一致事件 dispatch 时未注册任何 handler（FB1 / FB4）。

    例如 SettlementRequested 必须由 U05 finance 监听，若 U05 未部署则
    fail-fast，不允许 U04 业务流程产生不一致状态。
    """

    code = "MISSING_REQUIRED_HANDLER"
    status_code = 500
    message = "强一致事件缺失监听器，拒绝继续执行"
