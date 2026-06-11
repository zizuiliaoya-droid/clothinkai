"""U02 product 模块权限字符串常量。

按 nfr-design/logical-components.md §1.1 #19。
"""

from __future__ import annotations

# product:* — 款式 / SKU 操作
PRODUCT_READ = ("product", "read")
PRODUCT_WRITE = ("product", "write")
PRODUCT_DELETE = ("product", "delete")

# brand:* — 品牌字典操作
BRAND_READ = ("brand", "read")
BRAND_WRITE = ("brand", "write")
BRAND_DELETE = ("brand", "delete")


PRODUCT_PERMISSIONS: list[tuple[str, str, str]] = [
    # (scope, action, description)
    ("product", "read", "查询款式 / SKU"),
    ("product", "write", "创建 / 编辑款式 / SKU"),
    ("product", "delete", "软删 / 恢复款式 / SKU"),
    ("brand", "read", "查询品牌字典"),
    ("brand", "write", "创建 / 编辑品牌"),
    ("brand", "delete", "停用品牌"),
]


# U10b 平台商品映射 scope
SCOPE_PLATFORM_READ = "product.platform:read"
SCOPE_PLATFORM_WRITE = "product.platform:write"

# U17 套装/组合商品 scope
SCOPE_BUNDLE_READ = "product.bundle:read"
SCOPE_BUNDLE_WRITE = "product.bundle:write"
