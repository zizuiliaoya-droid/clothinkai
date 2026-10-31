from argparse import Namespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.core.exceptions import TenantContextMissingError
from app.modules.auth.create_tenant import run
from app.modules.auth.repository import AuditLogRepository
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import _login_fail_key
from app.modules.auth.tenant_onboarding import TenantOnboarding


def test_legacy_login_default_and_code_validation():
    assert LoginRequest(username="admin", password="x").tenant_code == "default"
    assert LoginRequest(tenant_code="  ", username="admin", password="x").tenant_code == "default"
    assert (
        LoginRequest(tenant_code=" tenant-a ", username="admin", password="x").tenant_code
        == "tenant-a"
    )
    with pytest.raises(ValidationError):
        LoginRequest(tenant_code="../bad", username="admin", password="x")


def test_login_fail_keys_separate_tenants():
    assert _login_fail_key("127.0.0.1", "admin") == "login:fail:127.0.0.1:admin"
    assert _login_fail_key("127.0.0.1", "admin", "a") != _login_fail_key("127.0.0.1", "admin", "b")


@pytest.mark.parametrize("code", ["", "  ", "../other", "a/b", "a:b", "x" * 65])
def test_provision_rejects_invalid_code(code):
    with pytest.raises(ValidationError):
        TenantOnboarding(code=code, name="测试")


@pytest.mark.asyncio
async def test_audit_rejects_missing_context_without_query():
    session = AsyncMock()
    with pytest.raises(TenantContextMissingError):
        await AuditLogRepository(session).query()
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_cli_refuses_wrong_target(capsys):
    args = Namespace(
        code="customer",
        name="测试",
        admin_username="admin",
        expect_environment="production",
        expect_database="wrong_database",
        apply=True,
    )
    with patch(
        "app.modules.auth.create_tenant.provision_tenant", new_callable=AsyncMock
    ) as provision:
        assert await run(args) == 2
        provision.assert_not_awaited()
    assert "不匹配" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cli_preview_does_not_open_database(capsys):
    args = Namespace(
        code="customer",
        name="测试",
        admin_username="admin",
        expect_environment="development",
        expect_database="preview_test",
        apply=False,
    )
    with (
        patch("app.modules.auth.create_tenant.settings") as config,
        patch("app.modules.auth.create_tenant.AsyncSessionBypass") as sessions,
    ):
        config.ENVIRONMENT = "development"
        config.DATABASE_URL_BYPASS = "postgresql+asyncpg://fake:fake@localhost/preview_test"
        assert await run(args) == 0
        sessions.assert_not_called()
    assert "initial_password" not in capsys.readouterr().out
