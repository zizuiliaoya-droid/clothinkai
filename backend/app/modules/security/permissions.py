"""安全模块权限 scope。

``security`` 是新的一级域，刻意不挂在 ``auth`` 下：``has()`` 的前缀通配只看 scope 第一段，
叫 ``auth.ip_allowlist`` 的话，将来谁拿到 ``auth.*:write``（比如为了管用户）就顺带拿到了
网络诊断与白名单的控制权。现有任何角色、任何 permission 行都捞不到 ``security.*``，
所以眼下只有持全局 ``*`` 的 admin / platform_admin 能过（``core/security/permissions.py`` 的 ``has()``）。

本批次（7b 第一步）不建 permission 行、不写迁移：诊断端点只给管理员，``*`` 已覆盖。
7f 做白名单本体时，迁移再 seed ``security.ip_allowlist:write`` 并只授给 admin。

常量只放 scope、不带 action：端点写 ``require_permission(SCOPE_IP_ALLOWLIST, "write")``。
不要照抄 auth 模块 ``require_permission("auth.user:write", "write")`` 的写法 ——
那样拼出来是 ``auth.user:write:write``，只能靠通配命中。
"""

from __future__ import annotations

SCOPE_IP_ALLOWLIST = "security.ip_allowlist"


__all__ = ["SCOPE_IP_ALLOWLIST"]
