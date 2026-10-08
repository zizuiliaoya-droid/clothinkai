"""U06a importer 模块权限字符串常量（NF-5）。

命名遵循 U01 Q12=B `module.sub:action`，对齐 default_roles.py 现有 importer.* 命名。

角色矩阵（default_roles + migration 010 seed）：
- importer.batch:read  → admin / operations / pr / pr_manager
- importer.batch:write → admin / pr / pr_manager
- importer.mapping:write → admin / pr_manager（字段映射限管理员场景）

以上是**默认来源**的权限。权限按来源判断（``importer/access.py::SOURCE_ACCESS``，8a-7）：
商品资料（manual_style_sku）的上传 / 重试 / 映射 / 查看 / 冲突裁决只认
``product.import:write`` → admin（``*``）/ merchandiser / operations（``product.*:*``）；
PR / 主管不再能导入商品资料（仍可凭 importer.batch:read 查看批次，成本价、采购价脱敏）。
博主（manual_blogger）的冲突裁决看 ``blogger:write``。
"""

from __future__ import annotations

IMPORTER_PERMISSIONS: list[tuple[str, str, str]] = [
    # (scope, action, description)
    ("importer.batch", "read", "查询导入批次 / 下载失败明细"),
    ("importer.batch", "write", "上传导入文件 / 重试批次"),
    ("importer.mapping", "write", "创建字段映射版本（管理员）"),
]
