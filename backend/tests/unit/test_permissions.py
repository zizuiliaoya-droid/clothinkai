"""单元测试：BR-PERM-001 权限合并算法（撤销 > 授予 > 角色）。"""

from __future__ import annotations

import pytest

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.domain import merge_permissions


@pytest.mark.unit
class TestMergePermissions:
    def test_role_only(self) -> None:
        """仅角色权限。"""
        result = merge_permissions(
            role_scopes={"auth.user:read", "auth.user:write"},
            grants=set(),
            revokes=set(),
        )
        assert result == frozenset({"auth.user:read", "auth.user:write"})

    def test_grant_extends_role(self) -> None:
        """grant 扩展角色权限。"""
        result = merge_permissions(
            role_scopes={"auth.user:read"},
            grants={"auth.audit:read"},
            revokes=set(),
        )
        assert result == frozenset({"auth.user:read", "auth.audit:read"})

    def test_revoke_overrides_role(self) -> None:
        """revoke 覆盖角色权限。"""
        result = merge_permissions(
            role_scopes={"auth.user:read", "auth.user:write"},
            grants=set(),
            revokes={"auth.user:write"},
        )
        assert result == frozenset({"auth.user:read"})

    def test_revoke_overrides_grant(self) -> None:
        """关键：revoke 优先级高于 grant。"""
        result = merge_permissions(
            role_scopes=set(),
            grants={"auth.user:write"},
            revokes={"auth.user:write"},
        )
        assert result == frozenset()

    def test_revoke_overrides_role_and_grant(self) -> None:
        """同时存在角色 + grant + revoke，revoke 最严。"""
        result = merge_permissions(
            role_scopes={"auth.user:write"},
            grants={"auth.user:write"},
            revokes={"auth.user:write"},
        )
        assert result == frozenset()


@pytest.mark.unit
class TestEffectivePermissionsHas:
    def test_exact_match(self) -> None:
        perms = EffectivePermissions(
            user_id="u1", scopes=frozenset({"auth.user:read"})
        )
        assert perms.has("auth.user", "read") is True
        assert perms.has("auth.user", "write") is False

    def test_wildcard_all(self) -> None:
        perms = EffectivePermissions(user_id="u1", scopes=frozenset({"*"}))
        assert perms.has("auth.user", "read") is True
        assert perms.has("anything", "anyaction") is True

    def test_module_wildcard_all_actions(self) -> None:
        perms = EffectivePermissions(
            user_id="u1", scopes=frozenset({"product.*:*"})
        )
        assert perms.has("product.style", "read") is True
        assert perms.has("product.sku", "write") is True
        assert perms.has("auth.user", "read") is False

    def test_module_wildcard_specific_action(self) -> None:
        perms = EffectivePermissions(
            user_id="u1", scopes=frozenset({"report.*:read"})
        )
        assert perms.has("report.publish_progress", "read") is True
        assert perms.has("report.style_roi", "read") is True
        # 写权限不通过通配符
        assert perms.has("report.publish_progress", "write") is False
