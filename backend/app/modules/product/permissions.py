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


# U10b 平台链接（千牛ID / 万相台主体ID）—— 运维视图专用。
# 刻意不挂在 product.* 下：EffectivePermissions.has 的前缀通配只看 scope 第一段，
# product.platform 会被跟单的 product.*:* 与运营的 product.*:read 命中，挡不住人。
SCOPE_PLATFORM_LINK = "ops.platform_link"
SCOPE_PLATFORM_LINK_READ = "ops.platform_link:read"
SCOPE_PLATFORM_LINK_WRITE = "ops.platform_link:write"

PLATFORM_LINK_PERMISSIONS: list[tuple[str, str, str]] = [
    ("ops.platform_link", "read", "查看平台链接映射（运维）"),
    ("ops.platform_link", "write", "维护平台链接与商品归属、渠道（运维）"),
]

# U17 套装/组合商品 scope
SCOPE_BUNDLE_READ = "product.bundle:read"
SCOPE_BUNDLE_WRITE = "product.bundle:write"
