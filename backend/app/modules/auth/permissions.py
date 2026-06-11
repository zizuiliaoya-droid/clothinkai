"""auth 模块的权限 scope 常量定义（按 functional-design BR-PERM Q12=B 命名规范 module.sub:action）。"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------
SCOPE_USER_READ = "auth.user:read"
SCOPE_USER_WRITE = "auth.user:write"
SCOPE_USER_DELETE = "auth.user:delete"

# ---------------------------------------------------------------------------
# 角色与权限管理
# ---------------------------------------------------------------------------
SCOPE_ROLE_ASSIGN = "auth.role:assign"
SCOPE_PERMISSION_GRANT = "auth.permission:grant"

# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------
SCOPE_AUDIT_READ = "auth.audit:read"

# ---------------------------------------------------------------------------
# 全部权限通配符
# ---------------------------------------------------------------------------
SCOPE_ALL = "*"

# 系统内置 permission 全集（启动时同步到 DB）
ALL_AUTH_SCOPES: tuple[str, ...] = (
    SCOPE_USER_READ,
    SCOPE_USER_WRITE,
    SCOPE_USER_DELETE,
    SCOPE_ROLE_ASSIGN,
    SCOPE_PERMISSION_GRANT,
    SCOPE_AUDIT_READ,
)
