"""U12 凭据模块权限 scope 常量。"""

from __future__ import annotations

SCOPE_READ = "credential"
SCOPE_WRITE = "credential"
SCOPE_DELETE = "credential"

# (scope, action, description) — migration 016 seed 用
CREDENTIAL_PERMISSIONS: list[tuple[str, str, str]] = [
    ("credential", "read", "查看平台凭据（不含密码）"),
    ("credential", "write", "创建/编辑/暂停/恢复平台凭据"),
    ("credential", "delete", "删除平台凭据"),
]


__all__ = ["CREDENTIAL_PERMISSIONS", "SCOPE_DELETE", "SCOPE_READ", "SCOPE_WRITE"]
