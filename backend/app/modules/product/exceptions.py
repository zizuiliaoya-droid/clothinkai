"""U02 product 模块业务异常。

继承自 core/exceptions.py 的 base 异常。错误码命名遵循 business-rules.md §8 矩阵。
"""

from __future__ import annotations

from app.core.exceptions import (
    AppException,
    DuplicateResourceError,
    FieldPermissionDenied,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationError,
)


# ---------------------------------------------------------------------------
# 唯一约束冲突
# ---------------------------------------------------------------------------


class StyleCodeConflictError(DuplicateResourceError):
    code = "STYLE_CODE_CONFLICT"
    status_code = 409


class SkuCodeConflictError(DuplicateResourceError):
    code = "SKU_CODE_CONFLICT"
    status_code = 409


class BrandCodeConflictError(DuplicateResourceError):
    code = "BRAND_CODE_CONFLICT"
    status_code = 409


# ---------------------------------------------------------------------------
# 引用与级联
# ---------------------------------------------------------------------------


class StyleHasActiveSkuError(AppException):
    """删除 style 但仍有启用的 sku（BR-U02-21）。"""

    code = "STYLE_HAS_ACTIVE_SKU"
    status_code = 409


class SkuHasReferenceError(AppException):
    """软删 sku 但已被 promotion / order 引用（BR-U02-20）。"""

    code = "SKU_HAS_REFERENCE"
    status_code = 409


# ---------------------------------------------------------------------------
# 业务校验
# ---------------------------------------------------------------------------


class InvalidStyleReferenceError(ValidationError):
    """sku.style_id 不存在 / 已软删（BR-U02-12）。"""

    code = "INVALID_STYLE_REFERENCE"


class InvalidBrandReferenceError(ValidationError):
    """style.brand_id 不存在 / 已停用。"""

    code = "INVALID_BRAND"


class InvalidAttachmentReferenceError(ValidationError):
    """main_image_id / detail_image_ids 不存在或跨租户。"""

    code = "INVALID_ATTACHMENT"


class SourcingPriceMismatchError(ValidationError):
    """sourcing_type 与价格字段不一致（BR-U02-13）。"""

    code = "SOURCING_PRICE_MISMATCH"


class InvalidPriceError(ValidationError):
    """价格字段不合法（< 0 或超过精度）。"""

    code = "INVALID_PRICE"


# ---------------------------------------------------------------------------
# 字段权限（U09 统一到 core/exceptions.py，此处 re-export 保持向后兼容）
# ---------------------------------------------------------------------------
# FieldPermissionDenied 由 core/exceptions.py 定义并 import（见上方 import 块）；
# blogger/promotion/finance 模块经 ``from app.modules.product.exceptions import
# FieldPermissionDenied`` re-export 链不变。


# ---------------------------------------------------------------------------
# 资源未找到（统一别名）
# ---------------------------------------------------------------------------


class StyleNotFoundError(ResourceNotFoundError):
    code = "STYLE_NOT_FOUND"


class SkuNotFoundError(ResourceNotFoundError):
    code = "SKU_NOT_FOUND"


class BrandNotFoundError(ResourceNotFoundError):
    code = "BRAND_NOT_FOUND"


__all__ = [
    "BrandCodeConflictError",
    "BrandNotFoundError",
    "FieldPermissionDenied",
    "InvalidAttachmentReferenceError",
    "InvalidBrandReferenceError",
    "InvalidPriceError",
    "InvalidStyleReferenceError",
    "SkuCodeConflictError",
    "SkuHasReferenceError",
    "SkuNotFoundError",
    "SourcingPriceMismatchError",
    "StyleCodeConflictError",
    "StyleHasActiveSkuError",
    "StyleNotFoundError",
]
