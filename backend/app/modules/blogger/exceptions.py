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
    """软删 blogger 但已有推广历史引用（BR-U03-20）。"""

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


class InvalidAccountFormatError(ValidationError):
    """编辑时把账号改成了不合格式的值（新建由 schema 挡；账号没变不校验）。"""

    code = "INVALID_ACCOUNT_FORMAT"


# ---------------------------------------------------------------------------
# 8b-3 标签字典与系统标签
# ---------------------------------------------------------------------------


class BloggerSystemTagReadonlyError(ValidationError):
    """改系统标签（quality_tags），或类目标签新加了系统标签词。"""

    code = "BLOGGER_SYSTEM_TAG_READONLY"


class BloggerTagNotInDictError(ValidationError):
    """类目标签新加的词不在启用的标签字典里（``details.tags``）。"""

    code = "BLOGGER_TAG_NOT_IN_DICT"


class BloggerTagReservedError(ValidationError):
    """系统标签不能进标签字典。"""

    code = "BLOGGER_TAG_RESERVED"


class BloggerTagExistsError(DuplicateResourceError):
    code = "BLOGGER_TAG_EXISTS"


class BloggerTagNotFoundError(ResourceNotFoundError):
    code = "BLOGGER_TAG_NOT_FOUND"


__all__ = [
    "BloggerHasReferenceError",
    "BloggerNotFoundError",
    "BloggerSystemTagReadonlyError",
    "BloggerTagExistsError",
    "BloggerTagNotFoundError",
    "BloggerTagNotInDictError",
    "BloggerTagReservedError",
    "BloggerXhsIdConflictError",
    "FieldPermissionDenied",  # re-exported from modules/product/exceptions
    "InvalidAccountFormatError",
    "InvalidFollowerCountError",
    "InvalidQuoteError",
    "InvalidTagFormatError",
]
