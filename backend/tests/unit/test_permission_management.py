"""个人权限管理的最小服务行为与 API 闸门（不连接数据库）。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import PermissionDeniedError, ResourceNotFoundError
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.api import router
from app.modules.auth.service import PermissionService

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("action", ["grant", "revoke", "reset"])
async def test_changes_invalidate_target_cache_and_audit(monkeypatch, action):
    tenant, user, actor, permission = uuid4(), uuid4(), uuid4(), uuid4()
    session = AsyncMock()
    service = PermissionService(session)
    service._users = SimpleNamespace(
        get_by_id=AsyncMock(
            return_value=SimpleNamespace(id=user, tenant_id=tenant, deleted_at=None)
        )
    )
    service._perms = SimpleNamespace(
        get_by_scope=AsyncMock(return_value=SimpleNamespace(id=permission)),
        upsert_override=AsyncMock(),
        delete_override=AsyncMock(return_value="revoke"),
    )
    service._audit = SimpleNamespace(log=AsyncMock())
    invalidate = AsyncMock()
    monkeypatch.setattr("app.modules.auth.service.invalidate_user_permissions_cache", invalidate)
    token = tenant_id_ctx.set(tenant)
    try:
        await getattr(service, action)(
            user, "negotiation_all:read", actor_id=actor, reason="主管请假"
        )
    finally:
        tenant_id_ctx.reset(token)
    if action == "reset":
        service._perms.delete_override.assert_awaited_once_with(user, permission)
        service._perms.upsert_override.assert_not_awaited()
    else:
        service._perms.upsert_override.assert_awaited_once_with(
            user, permission, effect=action, created_by=actor, reason="主管请假"
        )
    invalidate.assert_awaited_once_with(str(user))
    assert service._audit.log.await_args.kwargs["resource_id"] == user
    assert service._audit.log.await_args.kwargs["user_id"] == actor
    assert service._audit.log.await_args.kwargs["action"] == f"permission.{action}"
    session.commit.assert_awaited_once()


@pytest.mark.parametrize("action", ["grant", "revoke", "reset", "get_effective"])
async def test_other_tenant_target_is_not_found_even_if_identity_map_has_user(action):
    session = AsyncMock()
    service = PermissionService(session)
    service._users = SimpleNamespace(
        get_by_id=AsyncMock(return_value=SimpleNamespace(tenant_id=uuid4(), deleted_at=None))
    )
    service._perms = SimpleNamespace(get_by_scope=AsyncMock())
    token = tenant_id_ctx.set(uuid4())
    try:
        with pytest.raises(ResourceNotFoundError):
            if action == "get_effective":
                await service.get_effective(uuid4())
            else:
                await getattr(service, action)(uuid4(), "negotiation_all:read", actor_id=uuid4())
    finally:
        tenant_id_ctx.reset(token)
    service._perms.get_by_scope.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "path",
    [
        "/permissions",
        "/users/{user_id}/permissions/grant",
        "/users/{user_id}/permissions/revoke",
        "/users/{user_id}/permissions/reset",
        "/users/{user_id}/effective-permissions",
    ],
)
async def test_management_gate_accepts_exact_scope_and_rejects_read_only(path):
    route = next(route for route in router.routes if route.path == path)
    checker = route.dependencies[0].dependency
    allowed = EffectivePermissions(user_id="actor", scopes=frozenset({"auth.permission:grant"}))
    assert await checker(allowed) is allowed
    denied = EffectivePermissions(user_id="actor", scopes=frozenset({"auth.user:read"}))
    with pytest.raises(PermissionDeniedError):
        await checker(denied)
