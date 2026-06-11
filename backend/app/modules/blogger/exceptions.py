"""U03 blogger 模块业务异常。

按 nfr-design/logical-components.md §1.3 决策：
``FieldPermissionDenied`` 直接复用 ``modules/product/exceptions``，不重复定义。
U09 字段级权限落地后统一移到 ``core/exceptions.py``。
"""

from __future__ import annotations

from app.core.exceptions import (
    AppException,
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
# Re-export 复用的字段权限异常
from app.modules.product.exceptions import FieldPermissionDenied


# ---------------------------------------------------------------------------
# 唯一约束冲突
# ---------------------------------------------------------------------------


class BloggerXhsIdConflictError(DuplicateResourceError):
    """xiaohongshu_id 重复（含 details.existing_blogger_id 用于前端引导）。"""

    code = "BLOGGER_XHS_ID_CONFLICT"
    status_code = 409


# ---------------------------------------------------------------------------
# 资源未找到
# ---------------------------------------------------------------------------


class BloggerNotFoundError(ResourceNotFoundError):
    code = "BLOGGER_NOT_FOUND"


# ---------------------------------------------------------------------------
# 引用与级联
# ---------------------------------------------------------------------------


class BloggerHasReferenceError(AppException):
    """软删 blogger 但已被 promotion 引用（BR-U03-20）。U03 阶段不会触发。"""

    code = "BLOGGER_HAS_REFERENCE"
    status_code = 409


# ---------------------------------------------------------------------------
# 业务校验
# ---------------------------------------------------------------------------


class InvalidQuoteError(ValidationError):
    code = "INVALID_QUOTE"


class InvalidFollowerCountError(ValidationError):
    code = "INVALID_FOLLOWER_COUNT"


class InvalidTagFormatError(ValidationError):
    code = "INVALID_TAG_FORMAT"


__all__ = [
    "BloggerHasReferenceError",
    "BloggerNotFoundError",
    "BloggerXhsIdConflictError",
    "FieldPermissionDenied",  # re-exported from modules/product/exceptions
    "InvalidFollowerCountError",
    "InvalidQuoteError",
    "InvalidTagFormatError",
]
