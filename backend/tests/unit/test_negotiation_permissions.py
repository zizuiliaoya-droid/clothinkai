"""谈款审核权限隔离（纯规则测试，不碰数据库）。

这里守的是一个很容易踩回去的坑：``EffectivePermissions.has`` 的前缀通配只看 scope
第一段。谈款审核的 scope 如果挂在 ``promotion.`` 下，PR 持有的 ``promotion.*:*``
会把审核权一并给了，审核这道关就形同虚设。
"""

from __future__ import annotations

from uuid import uuid4

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES


def _perms(*scopes: str) -> EffectivePermissions:
    return EffectivePermissions(user_id=str(uuid4()), scopes=frozenset(scopes))


def _role_scopes(code: str) -> frozenset[str]:
    for role in DEFAULT_ROLES:
        if role.code == code:
            return frozenset(role.permissions)
    raise AssertionError(f"角色 {code} 不在默认角色表里")


class TestNegotiationScopeIsolation:
    def test_promotion_wildcard_does_not_grant_negotiation(self) -> None:
        """PR 的 promotion.*:* 不能命中谈款审核 —— 这是独立一级域的全部理由。"""
        perms = _perms("promotion.*:*")
        assert perms.has("negotiation", "read") is False
        assert perms.has("negotiation", "write") is False
        assert perms.has("negotiation.review", "approve") is False

    def test_negotiation_write_does_not_grant_review(self) -> None:
        """能建单、能提交，但不能自己批。"""
        perms = _perms("negotiation:read", "negotiation:write")
        assert perms.has("negotiation", "read") is True
        assert perms.has("negotiation", "write") is True
        assert perms.has("negotiation.review", "approve") is False

    def test_wildcard_negotiation_would_grant_review(self) -> None:
        """反证：给了 negotiation.*:* 就会把审核权一并给出去。

        所以默认角色表里刻意只给 PR 两条具体 scope，不给通配。
        """
        perms = _perms("negotiation.*:*")
        assert perms.has("negotiation.review", "approve") is True

    def test_review_scope_alone_grants_only_review(self) -> None:
        perms = _perms("negotiation.review:approve")
        assert perms.has("negotiation.review", "approve") is True
        assert perms.has("negotiation", "write") is False

    def test_admin_wildcard_grants_everything(self) -> None:
        perms = _perms("*")
        assert perms.has("negotiation", "write") is True
        assert perms.has("negotiation.review", "approve") is True


class TestDefaultRoleGrants:
    def test_pr_can_write_but_not_review(self) -> None:
        perms = _perms(*_role_scopes("pr"))
        assert perms.has("negotiation", "read") is True
        assert perms.has("negotiation", "write") is True
        assert perms.has("negotiation.review", "approve") is False

    def test_pr_manager_can_review(self) -> None:
        perms = _perms(*_role_scopes("pr_manager"))
        assert perms.has("negotiation", "write") is True
        assert perms.has("negotiation.review", "approve") is True

    def test_finance_is_read_only(self) -> None:
        """PRD：财务只读查看。"""
        perms = _perms(*_role_scopes("finance"))
        assert perms.has("negotiation", "read") is True
        assert perms.has("negotiation", "write") is False
        assert perms.has("negotiation.review", "approve") is False

    def test_roles_outside_the_flow_have_no_access(self) -> None:
        for code in ("designer", "merchandiser", "warehouse", "pattern_maker"):
            perms = _perms(*_role_scopes(code))
            assert perms.has("negotiation", "read") is False, f"{code} 不该能看谈款单"
            assert perms.has("negotiation", "write") is False, f"{code} 不该能改谈款单"
