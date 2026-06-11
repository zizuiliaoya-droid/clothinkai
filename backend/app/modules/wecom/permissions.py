"""U07 wecom 模块权限字符串常量（命名遵循 U01 `module.sub:action`）。

角色矩阵（default_roles + migration 011 seed）：
- wecom.config:write    → admin
- wecom.bind:write      → admin / pr / pr_manager
- wecom.template:write  → admin
- wecom.message:read    → admin / pr / pr_manager / operations
- notification:read     → 全部登录用户（限本人；admin / pr / pr_manager / operations / finance ...）
"""

from __future__ import annotations


WECOM_PERMISSIONS: list[tuple[str, str, str]] = [
    # (scope, name/description, category)
    ("wecom.config:write", "配置企微自建应用", "function"),
    ("wecom.bind:write", "绑定博主企微外部联系人", "function"),
    ("wecom.template:write", "编辑催发消息模板", "function"),
    ("wecom.message:read", "查询企微消息记录", "function"),
    ("wecom.alert_config:read", "查看企微预警配置", "function"),
    ("wecom.alert_config:write", "编辑企微预警配置", "function"),
    ("notification:read", "查询本人站内通知", "function"),
]


__all__ = ["WECOM_PERMISSIONS"]
