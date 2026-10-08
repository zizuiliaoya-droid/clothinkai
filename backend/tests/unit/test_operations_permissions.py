"""8a-7：运营可维护商品资料（AC 54 ~ 60 的权限部分，设计 §10）。

运营 = 原权限 + ``product.*:*``（``PRODUCT_READ`` 保留）。``has()`` 的通配只看 scope 第一段，
所以逐项比对「所有路由上声明的 (scope, action)」在新旧 scope 集合下的判定：只允许第一段是
``product`` 的不同（AC 60）。
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.core.security.field_permissions import (
    FieldPermissionContext,
    can_read_field,
    can_write_field,
)
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES

# 058 之前 default_roles.py 里运营的 permissions（写死，作为基线）
_OPS_BASELINE_DEFAULT: tuple[str, ...] = (
    "report.*:read",
    "promotion.*:read",
    "blogger.*:read",
    "product.*:read",
    "importer.*:read",
    "wecom.message:read",
    "notification:read",
    "ops.platform_link:read",
    "ops.platform_link:write",
    "negotiation:read",
)

# 056 测试库里运营实际持有的 scope（历次迁移给现存库补授的也在内；写死，作为基线）
_OPS_BASELINE_DB: tuple[str, ...] = (
    "ai.advice:read",
    "blogger.*:read",
    "brand.*:read",
    "credential:read",
    "data_quality:read",
    "design.design:read",
    "importer.*:read",
    "importer.batch:read",
    "negotiation:read",
    "notification:read",
    "ops.platform_link:read",
    "ops.platform_link:write",
    "product.*:read",
    "promotion.*:read",
    "report.*:read",
    "report.export:read",
    "report.store_daily:write",
    "wecom.alert_config:read",
    "wecom.alert_config:write",
    "wecom.message:read",
)

# 不在路由依赖上、而在处理函数 / service 里用 has() 判断的 scope（导入来源级权限等）
_IN_CODE_CHECKS: tuple[tuple[str, str], ...] = (
    ("product.import", "write"),
    ("importer.batch", "read"),
    ("importer.batch", "write"),
    ("importer.mapping", "write"),
    ("blogger", "write"),
    ("security.ip_allowlist", "read"),
    ("security.ip_allowlist", "write"),
)


def _ops_now() -> tuple[str, ...]:
    return next(r for r in DEFAULT_ROLES if r.code == "operations").permissions


def _perms(scopes: tuple[str, ...] | set[str]) -> EffectivePermissions:
    return EffectivePermissions(user_id="ops", scopes=frozenset(scopes))


def _walk(dependant: Dependant) -> Iterator[Dependant]:
    """递归遍历全部（含嵌套）依赖；get_flat_dependant 在 FastAPI 0.115 不保留 dependencies。"""
    for sub in dependant.dependencies:
        yield sub
        yield from _walk(sub)


def _route_scopes() -> set[tuple[str, str]]:
    """所有路由（含嵌套依赖）上 require_permission 声明的 (scope, action)。"""
    from app.main import app

    found: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for dep in _walk(route.dependant):
            call = dep.call
            if getattr(call, "__qualname__", "") != "require_permission.<locals>._checker":
                continue
            nonlocals = inspect.getclosurevars(call).nonlocals  # type: ignore[arg-type]
            found.add((nonlocals["scope"], nonlocals["action"]))
    return found


@pytest.mark.unit
class TestOperationsRoleSpec:
    def test_product_all_added_and_read_kept(self) -> None:
        perms = _ops_now()
        assert "product.*:*" in perms
        assert "product.*:read" in perms
        assert set(perms) - set(_OPS_BASELINE_DEFAULT) == {"product.*:*"}
        assert set(_OPS_BASELINE_DEFAULT) <= set(perms)

    def test_description(self) -> None:
        role = next(r for r in DEFAULT_ROLES if r.code == "operations")
        assert (
            role.description
            == "看报表与店铺数据，维护平台链接与商品资料（商品 / 套装 / 款式 / 成本表）"
        )

    @pytest.mark.parametrize(
        ("scope", "action"),
        [
            ("product", "read"),
            ("product", "write"),
            ("product", "delete"),
            ("product.goods", "read"),
            ("product.goods", "write"),
            ("product.import", "write"),
            ("product.style", "read"),
        ],
    )
    def test_new_product_capabilities(self, scope: str, action: str) -> None:
        assert _perms(_ops_now()).has(scope, action)

    @pytest.mark.parametrize(
        ("scope", "action"),
        [
            ("negotiation", "write"),
            ("negotiation.review", "approve"),
            ("promotion", "write"),
            ("promotion.urge", "write"),
            ("promotion.retro", "write"),
            ("promotion.warehouse", "write"),
            ("promotion.review", "approve"),
            ("settlement", "write"),
            ("finance.settlement", "write"),
            ("finance.settlement", "pay"),
            ("blogger", "write"),
            ("importer.batch", "write"),
            ("importer.mapping", "write"),
            ("security.ip_allowlist", "read"),
            ("security.ip_allowlist", "write"),
            ("auth.user", "write"),
            ("urge_config", "write"),
        ],
    )
    def test_process_writes_still_denied(self, scope: str, action: str) -> None:
        assert not _perms(_ops_now()).has(scope, action)
        assert not _perms(set(_OPS_BASELINE_DB) | {"product.*:*"}).has(scope, action)


@pytest.mark.unit
class TestNoSpillOutsideProduct:
    """AC 60：除第一段是 product 的 scope 外，判定与基线完全一致。"""

    def test_routes_found(self) -> None:
        scopes = _route_scopes()
        # 前提：确实从路由闭包里取到了声明（不然下面的比对是空转）
        assert len(scopes) > 50
        assert ("product", "write") in scopes
        assert ("ops.platform_link", "write") in scopes

    @pytest.mark.parametrize(
        ("baseline", "now"),
        [
            (_OPS_BASELINE_DEFAULT, None),
            (_OPS_BASELINE_DB, (*_OPS_BASELINE_DB, "product.*:*")),
        ],
        ids=["default_roles", "db_056"],
    )
    def test_only_product_domain_differs(
        self, baseline: tuple[str, ...], now: tuple[str, ...] | None
    ) -> None:
        old = _perms(baseline)
        new = _perms(now if now is not None else _ops_now())
        checks = _route_scopes() | set(_IN_CODE_CHECKS)
        changed = {(s, a) for s, a in checks if old.has(s, a) != new.has(s, a)}
        assert changed, "运营应该多了商品写权限"
        outside = {(s, a) for s, a in changed if s.split(".", 1)[0] != "product"}
        assert outside == set()
        # 只会多、不会少
        assert all(new.has(s, a) and not old.has(s, a) for s, a in changed)


@pytest.mark.unit
class TestOperationsFieldPermissions:
    @staticmethod
    def _ctx(*roles: str) -> FieldPermissionContext:
        return FieldPermissionContext(
            role_codes=frozenset(roles), grants=frozenset(), revokes=frozenset()
        )

    @pytest.mark.parametrize("field", ["cost_price", "purchase_price"])
    def test_operations_read_write(self, field: str) -> None:
        ctx = self._ctx("operations")
        assert can_read_field("sku", field, ctx)
        assert can_write_field("sku", field, ctx)

    @pytest.mark.parametrize("field", ["cost_price", "purchase_price"])
    @pytest.mark.parametrize("role", ["designer", "design_assistant", "pr", "pr_manager"])
    def test_others_unchanged(self, field: str, role: str) -> None:
        ctx = self._ctx(role)
        assert not can_read_field("sku", field, ctx)
        assert not can_write_field("sku", field, ctx)

    def test_blogger_contact_still_hidden_from_operations(self) -> None:
        ctx = self._ctx("operations")
        for field in ("quote", "wechat", "phone"):
            assert not can_read_field("blogger", field, ctx)
