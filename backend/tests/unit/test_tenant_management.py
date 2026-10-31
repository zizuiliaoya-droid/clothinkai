"""平台身份的最小非数据库边界与运维预览。"""

from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.core.exceptions import PermissionDeniedError
from app.core.security.permissions import EffectivePermissions
from app.modules.auth import grant_platform_admin, platform_access
from app.modules.auth.schemas import UserCreate
from app.modules.auth.service import UserService
from app.modules.auth.tenant_management import TenantManagementService
from app.modules.auth.tenant_onboarding import TenantOnboarding

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize(
    "before,after",
    [
        ([], ["platform_admin"]),
        (["admin"], ["admin", "platform_admin"]),
        (["admin", "platform_admin"], ["admin"]),
    ],
)
async def test_regular_role_editor_cannot_change_platform_membership(before, after):
    with pytest.raises(PermissionDeniedError):
        platform_access.preserve_platform_membership(before, after)


async def test_platform_membership_can_stay_while_business_roles_change():
    platform_access.preserve_platform_membership(
        ["admin", "platform_admin"], ["finance", "pr_manager", "platform_admin"]
    )


async def test_create_user_cannot_request_platform_role_before_any_write():
    service = UserService(AsyncMock())
    service._users = SimpleNamespace(get_by_username=AsyncMock())
    with pytest.raises(PermissionDeniedError):
        await service.create(UserCreate(username="new_admin", role_codes=["platform_admin"]))
    service._users.get_by_username.assert_not_awaited()
    service._session.commit.assert_not_awaited()


@pytest.mark.parametrize("allowed", [True, False])
async def test_platform_target_requires_real_platform_actor(monkeypatch, allowed):
    monkeypatch.setattr(platform_access, "has_platform_role", AsyncMock(return_value=True))
    monkeypatch.setattr(platform_access, "is_platform_admin", AsyncMock(return_value=allowed))
    if allowed:
        await platform_access.protect_platform_user(AsyncMock(), uuid4(), uuid4())
    else:
        with pytest.raises(PermissionDeniedError):
            await platform_access.protect_platform_user(AsyncMock(), uuid4(), uuid4())


async def test_bootstrap_preview_checks_target_without_database(monkeypatch, capsys):
    monkeypatch.setattr(
        grant_platform_admin,
        "settings",
        SimpleNamespace(
            ENVIRONMENT="development",
            DATABASE_URL_BYPASS="postgresql+asyncpg://synthetic:synthetic@127.0.0.1:1/trial",
        ),
    )
    sessions = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(grant_platform_admin, "AsyncSessionBypass", sessions)
    args = Namespace(
        username="admin", expect_environment="production", expect_database="trial", apply=False
    )
    assert await grant_platform_admin.run(args) == 2
    args.expect_environment = "development"
    assert await grant_platform_admin.run(args) == 0
    sessions.assert_not_called()
    assert "synthetic:synthetic" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "action,denied",
    [("list", "*"), ("create", "*"), ("status", "*"), ("create", "auth.user:write")],
)
async def test_platform_identity_does_not_override_personal_denial(monkeypatch, action, denied):
    identity = AsyncMock()
    monkeypatch.setattr("app.modules.auth.tenant_management.require_platform_admin", identity)
    actor = SimpleNamespace(id=uuid4())
    permissions = EffectivePermissions(str(actor.id), frozenset({"*"}), frozenset({denied}))
    monkeypatch.setattr(
        "app.modules.auth.service.AuthService.load_effective_permissions",
        AsyncMock(return_value=permissions),
    )
    session = AsyncMock()
    service = TenantManagementService(session)
    with pytest.raises(PermissionDeniedError):
        if action == "list":
            await service.list(actor, page=1, page_size=20)
        elif action == "create":
            await service.create(actor, TenantOnboarding(code="denied", name="Denied"))
        else:
            await service.set_status(actor, uuid4(), "disabled")
    identity.assert_awaited_once_with(session, actor.id)
    session.execute.assert_not_awaited()
