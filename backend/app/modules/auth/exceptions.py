"""auth 模块特定异常。"""

from __future__ import annotations

from app.core.exceptions import AppException


class UsernameAlreadyExistsError(AppException):
    code = "USERNAME_ALREADY_EXISTS"
    status_code = 409
    message = "该用户名已存在"


class RoleNotFoundError(AppException):
    code = "ROLE_NOT_FOUND"
    status_code = 404
    message = "角色不存在"


class WeakPasswordError(AppException):
    code = "WEAK_PASSWORD"
    status_code = 422
    message = "密码强度不符合要求（≥10字符 + 大小写 + 数字）"


class CannotUnlockUserError(AppException):
    code = "CANNOT_UNLOCK_USER"
    status_code = 422
    message = "该用户未被锁定，无法解锁"
