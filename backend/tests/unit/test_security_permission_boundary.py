"""``security.ip_allowlist`` 的权限边界（纯规则，不连库）。

网络诊断会回显原始转发头（暴露内部网络结构），后续的登录 IP 白名单也挂在同一个 scope 上。
``security`` 是新一级域：现有角色手里的通配（``auth.*:*``、``report.*:read``、``promotion.*:*`` ……）
都捞不到它，眼下只有持全局 ``*`` 的 admin / platform_admin 能过。这里把这件事钉死，
防止以后有人「为了统一」把它挂到 ``auth.`` 下面。
"""

from __future__ import annotations

import pytest

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES, all_builtin_permission_scopes
from app.modules.security.permissions import SCOPE_IP_ALLOWLIST

_ACTION = "write"


def has(scopes: set[str]) -> bool:
    return EffectivePermissions(user_id="t", scopes=frozenset(scopes)).has(
        SCOPE_IP_ALLOWLIST, _ACTION
    )


class TestScopeShape:
    def test_scope_is_new_top_level_domain(self) -> None:
        assert SCOPE_IP_ALLOWLIST == "security.ip_allowlist"

    def test_scope_constant_has_no_action(self) -> None:
        """常量不带 action：带了会拼成 ``security.ip_allowlist:write:write``，只能靠通配命中。"""
        assert ":" not in SCOPE_IP_ALLOWLIST

    def test_no_builtin_permission_in_security_domain(self) -> None:
        """本批次不建 permission 行：预设角色矩阵里不该出现任何 security.* scope。"""
        assert not [s for s in all_builtin_permission_scopes() if s.startswith("security")]


class TestDefaultRoleMatrix:
    def test_only_admin_and_platform_admin_pass(self) -> None:
        passing = {role.code for role in DEFAULT_ROLES if has(set(role.permissions))}
        assert passing == {"admin", "platform_admin"}


class TestWildcardDoesNotLeak:
    @pytest.mark.parametrize(
        "granted",
        [
            "auth.*:*",
            "auth.*:write",
            "report.*:read",
            "promotion.*:*",
            "wecom.*:*",
            "security.ip_allowlist:read",
        ],
    )
    def test_other_grants_cannot_pass(self, granted: str) -> None:
        assert not has({granted})

    @pytest.mark.parametrize(
        "granted",
        [
            "*",
            "security.ip_allowlist:write",
            # 下面两条：security 一级域在 permission 表里还不存在，覆盖授权要求 scope 先存在，
            # 所以眼下没人能持有；这里只确认规则本身
            "security.*:write",
            "security.*:*",
        ],
    )
    def test_matching_grants_pass(self, granted: str) -> None:
        assert has({granted})
