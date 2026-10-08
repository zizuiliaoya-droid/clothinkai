"""款式下拉读权限（7a-1，纯规则测试，不碰数据库）。

PR 在新建推广 / 谈款 / 录入信息里要选款式、归属商品、颜色及规格，这三个下拉
原来挂 ``product:read``，PR 一律 403。改法是开一个窄 scope ``product.style:read``
只授给 pr / pr_manager，而不是给 PR ``product.*:read``——后者会连带放开成本表
``/api/skus/``、字典等整个 product 域。

这里守三件事：
1. 原来能读款式列表的角色一个都不能丢（``product:read`` ⇒ ``product.style:read``）；
2. PR 与主管只多了这一条，不会顺带拿到 product 域的其他读权限；
3. 路由上真实挂的依赖确实是新 scope，其余 product 端点没被顺手放开。
"""

from __future__ import annotations

import inspect
from typing import Any
from uuid import uuid4

import pytest

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.product.api import router


def _perms(*scopes: str) -> EffectivePermissions:
    return EffectivePermissions(user_id=str(uuid4()), scopes=frozenset(scopes))


def _role_scopes(code: str) -> frozenset[str]:
    for role in DEFAULT_ROLES:
        if role.code == code:
            return frozenset(role.permissions)
    raise AssertionError(f"角色 {code} 不在默认角色表里")


def _route_scope(path: str, method: str) -> tuple[str, str]:
    """取路由上 require_permission 闭包里的 (scope, action)。"""
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            dependency: Any = route.dependencies[0].dependency  # type: ignore[attr-defined]
            nonlocals = inspect.getclosurevars(dependency).nonlocals
            return nonlocals["scope"], nonlocals["action"]
    raise AssertionError(f"没找到路由 {method} {path}")


class TestDefaultRoleGrants:
    def test_no_role_loses_style_read(self) -> None:
        """原来能读款式列表的角色，改挂新 scope 后都还能读。"""
        for role in DEFAULT_ROLES:
            perms = _perms(*role.permissions)
            if perms.has("product", "read"):
                assert perms.has("product.style", "read") is True, f"{role.code} 丢了款式读权限"

    @pytest.mark.parametrize("code", ["pr", "pr_manager"])
    def test_pr_and_manager_get_style_read_only(self, code: str) -> None:
        scopes = _role_scopes(code)
        perms = _perms(*scopes)
        assert perms.has("product.style", "read") is True
        # 只多这一条：成本表、商品 / 套装、SKU 仍然读不到
        assert perms.has("product", "read") is False
        assert perms.has("product.goods", "read") is False
        assert perms.has("product.sku", "read") is False
        for wide in ("product.*:*", "product.*:read", "product:read"):
            assert wide not in scopes, f"{code} 不该持有 {wide}"

    @pytest.mark.parametrize("code", ["finance", "warehouse", "pattern_maker"])
    def test_other_roles_unchanged(self, code: str) -> None:
        perms = _perms(*_role_scopes(code))
        assert perms.has("product.style", "read") is False
        assert perms.has("product", "read") is False


class TestRouteScopes:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/styles/",
            "/api/styles/{style_id}/goods",
            "/api/skus/by-style/{style_id}",
        ],
    )
    def test_dropdown_routes_use_style_scope(self, path: str) -> None:
        assert _route_scope(path, "GET") == ("product.style", "read")

    @pytest.mark.parametrize(
        "path",
        [
            "/api/skus/",
            "/api/styles/{style_id}",
            "/api/styles/match",
        ],
    )
    def test_other_read_routes_unchanged(self, path: str) -> None:
        assert _route_scope(path, "GET") == ("product", "read")
