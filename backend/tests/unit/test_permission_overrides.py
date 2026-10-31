"""个人拒绝必须在功能、字段、矩阵与序列化路径一致生效。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import PermissionDeniedError, ResourceNotFoundError
from app.core.security.field_permissions import (
    can_read_field,
    can_write_field,
    field_perm_context_from,
)
from app.core.security.permissions import EffectivePermissions, _load_from_cache, _save_to_cache
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.api import create_user, router, user_management_capabilities
from app.modules.auth.schemas import UserCreate
from app.modules.auth.service import AuthService, PermissionService, UserService
from app.modules.flow.actor import load_flow_actor
from app.modules.flow.matrix import ExplicitScope

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "grant", ["*", "promotion.*:*", "promotion.*:write", "promotion.item:write"]
)
@pytest.mark.parametrize(
    "deny", ["*", "promotion.*:*", "promotion.*:write", "promotion.item:write"]
)
def test_deny_wins_over_every_grant(grant, deny):
    perms = EffectivePermissions("u", frozenset({grant}), frozenset({deny}))
    assert not perms.has("promotion.item", "write")
    assert not perms.has_explicit("promotion.item", "write")


def test_exact_deny_preserves_other_actions_and_resources():
    perms = EffectivePermissions("u", frozenset({"*"}), frozenset({"promotion.item:write"}))
    assert not perms.has("promotion.item", "write")
    assert perms.has("promotion.item", "read")
    assert perms.has("promotion.other", "write")
    assert perms.has("finance.settlement", "pay")


@pytest.mark.parametrize("deny", ["*", "field.*:*", "field.*:read", "field.sku.cost_price:read"])
def test_field_deny_wins_over_superuser_and_role(deny):
    ctx = field_perm_context_from(
        frozenset({"admin"}), {"*"}, {"field.sku.cost_price:read"}, {deny}
    )
    assert not can_read_field("sku", "cost_price", ctx)
    if deny.endswith(":read"):
        assert can_write_field("sku", "cost_price", ctx)


@pytest.mark.asyncio
async def test_runtime_loaders_and_explicit_matrix_keep_revokes():
    user_id = uuid4()
    repo = SimpleNamespace(
        list_scopes_for_user=AsyncMock(
            return_value=(
                {"negotiation.review:approve", "promotion.*:*"},
                set(),
                {"negotiation.*:*", "promotion.item:write"},
            )
        )
    )
    service = AuthService(AsyncMock())
    service._permissions = repo
    perms = await service.load_effective_permissions(user_id)
    assert not perms.has("promotion.item", "write")
    roles = SimpleNamespace(list_codes_for_user=AsyncMock(return_value=["pr_manager"]))
    actor = await load_flow_actor(user_id, roles, repo)
    assert not ExplicitScope("negotiation.review", "approve").ok(actor, None)
    assert actor.perms.has("promotion.item", "read")


@pytest.mark.asyncio
async def test_cache_round_trip_preserves_denies_and_ignores_old_shape(monkeypatch):
    cache = AsyncMock()
    monkeypatch.setattr("app.core.security.permissions.cache", cache)
    perms = EffectivePermissions("u", frozenset({"*"}), frozenset({"auth.role:assign"}))
    await _save_to_cache(perms)
    cache.get.return_value = cache.setex.await_args.args[2]
    loaded = await _load_from_cache("u")
    assert loaded == perms
    assert not loaded.has("auth.role", "assign")
    cache.get.return_value = '["*"]'
    assert await _load_from_cache("u") is None


@pytest.mark.asyncio
async def test_effective_view_excludes_revoked_concrete_permission(monkeypatch):
    monkeypatch.setattr(
        "app.modules.auth.platform_access.has_platform_role", AsyncMock(return_value=False)
    )
    tenant, user = uuid4(), uuid4()
    svc = PermissionService(AsyncMock())
    svc._users = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(tenant_id=tenant, deleted_at=None))
    )
    svc._perms = SimpleNamespace(
        list_scopes_for_user=AsyncMock(return_value=({"*"}, set(), {"auth.role:assign"})),
        list_registered=AsyncMock(
            return_value=[
                SimpleNamespace(scope=s) for s in ["*", "auth.role:assign", "auth.user:read"]
            ]
        ),
    )
    token = tenant_id_ctx.set(tenant)
    try:
        view = await svc.get_effective(user)
    finally:
        tenant_id_ctx.reset(token)
    assert view["effective"] == ["auth.user:read"]
    assert view["revokes"] == ["auth.role:assign"]


@pytest.mark.asyncio
async def test_role_capability_and_write_gate_use_effective_permission():
    allowed = EffectivePermissions("u", frozenset({"auth.role:assign"}))
    denied = EffectivePermissions("u", frozenset({"*"}), frozenset({"auth.role:assign"}))
    assert await user_management_capabilities(allowed) == {
        "can_assign_roles": True,
        "can_create_users": False,
    }
    assert await user_management_capabilities(denied) == {
        "can_assign_roles": False,
        "can_create_users": False,
    }
    route = next(r for r in router.routes if r.path == "/users/{user_id}/roles")
    checker = route.dependencies[0].dependency
    assert await checker(allowed) is allowed
    with pytest.raises(PermissionDeniedError):
        await checker(denied)


@pytest.mark.asyncio
async def test_role_edit_rejects_cross_tenant_identity_map_target():
    svc = UserService(AsyncMock())
    svc._users = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(tenant_id=uuid4(), deleted_at=None))
    )
    svc._roles = SimpleNamespace(list_by_codes=AsyncMock())
    token = tenant_id_ctx.set(uuid4())
    try:
        with pytest.raises(ResourceNotFoundError):
            await svc.assign_roles(uuid4(), ["finance", "pr_manager"])
    finally:
        tenant_id_ctx.reset(token)
    svc._roles.list_by_codes.assert_not_awaited()
    svc._session.commit.assert_not_awaited()


def test_legacy_full_scope_argument_cannot_bypass_specific_deny():
    allowed = EffectivePermissions("u", frozenset({"auth.user:write"}))
    assert allowed.has("auth.user:write", "write")
    denied = EffectivePermissions("u", frozenset({"*"}), frozenset({"auth.user:write"}))
    assert not denied.has("auth.user:write", "write")


@pytest.mark.asyncio
async def test_create_with_roles_cannot_bypass_role_assignment_deny():
    session = AsyncMock()
    denied = EffectivePermissions("u", frozenset({"*"}), frozenset({"auth.role:assign"}))
    with pytest.raises(PermissionDeniedError):
        await create_user(UserCreate(username="new_admin", role_codes=["admin"]), session, denied)
    session.commit.assert_not_awaited()
