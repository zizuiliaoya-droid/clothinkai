"""集成测试：EP01-S01 登录主路径 + 失败 + 限流 + 锁定（BR-AUTH-001/002）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    InvalidCredentialsError,
    RateLimitedError,
)
from app.core.security.auth import hash_password
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import User
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import AuthService

PASSWORD = "Password123"


@pytest.fixture
def stub_cache() -> AsyncMock:
    """Stub Redis 客户端：内存计数。"""
    store: dict[str, int] = {}

    async def _get(key: str) -> str | None:
        return str(store[key]) if key in store else None

    async def _incr(key: str) -> int:
        store[key] = store.get(key, 0) + 1
        return store[key]

    async def _expire(_key: str, _ttl: int) -> bool:
        return True

    async def _delete(*keys: str) -> int:
        deleted = 0
        for k in keys:
            if k in store:
                del store[k]
                deleted += 1
        return deleted

    async def _ttl(key: str) -> int:
        return 900 if key in store else -2

    mock = AsyncMock()
    mock.get.side_effect = _get
    mock.incr.side_effect = _incr
    mock.expire.side_effect = _expire
    mock.delete.side_effect = _delete
    mock.ttl.side_effect = _ttl
    mock.exists = AsyncMock(return_value=False)
    mock.setex = AsyncMock()
    return mock


@pytest.mark.integration
@pytest.mark.asyncio
class TestLogin:
    async def test_login_success(
        self,
        session: AsyncSession,
        tenant_a: User,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(  # type: ignore[attr-defined]
                tenant_a, username="alice", password_hash=hash_password(PASSWORD)
            )
            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                svc = AuthService(session)
                access, refresh, ret_user, must_change = await svc.login(
                    LoginRequest(username="alice", password=PASSWORD),
                    ip="127.0.0.1",
                    user_agent="pytest",
                )

            assert access
            assert refresh
            assert ret_user.id == user.id
            assert must_change is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_login_invalid_password(
        self,
        session: AsyncSession,
        tenant_a: User,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await factory.user(  # type: ignore[attr-defined]
                tenant_a, username="bob", password_hash=hash_password(PASSWORD)
            )
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = AuthService(session)
                with pytest.raises(InvalidCredentialsError):
                    await svc.login(
                        LoginRequest(username="bob", password="WrongPwd123"),
                        ip="127.0.0.1",
                    )
        finally:
            tenant_id_ctx.reset(token)

    async def test_login_unknown_user(
        self, session: AsyncSession, stub_cache: AsyncMock
    ) -> None:
        with patch("app.modules.auth.service.cache", stub_cache):
            svc = AuthService(session)
            with pytest.raises(InvalidCredentialsError):
                await svc.login(
                    LoginRequest(username="nonexistent", password="x"),
                    ip="127.0.0.1",
                )

    async def test_rate_limit_after_5_failures(
        self,
        session: AsyncSession,
        tenant_a: User,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        """BR-AUTH-001：5 次失败后限流 429。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await factory.user(  # type: ignore[attr-defined]
                tenant_a, username="charlie", password_hash=hash_password(PASSWORD)
            )
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = AuthService(session)
                # 5 次失败
                for _ in range(5):
                    with pytest.raises(InvalidCredentialsError):
                        await svc.login(
                            LoginRequest(username="charlie", password="WrongPwd123"),
                            ip="1.2.3.4",
                        )
                # 第 6 次应限流
                with pytest.raises(RateLimitedError):
                    await svc.login(
                        LoginRequest(username="charlie", password=PASSWORD),
                        ip="1.2.3.4",
                    )
        finally:
            tenant_id_ctx.reset(token)

    async def test_account_locked_after_10_failures(
        self,
        session: AsyncSession,
        tenant_a: User,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        """BR-AUTH-002：账户级累计 10 次失败 → locked_at 非空 → 423。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(  # type: ignore[attr-defined]
                tenant_a, username="dave", password_hash=hash_password(PASSWORD)
            )
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = AuthService(session)
                # 制造 10 次跨 IP 失败（绕过 IP+username 限流）
                for i in range(10):
                    with pytest.raises(InvalidCredentialsError):
                        await svc.login(
                            LoginRequest(username="dave", password="WrongPwd123"),
                            ip=f"10.0.0.{i}",
                        )

            await session.refresh(user)
            assert user.failed_login_count >= 10
            assert user.locked_at is not None

            # 第 11 次（即使密码对）也返回 423
            with patch("app.modules.auth.service.cache", stub_cache):
                svc2 = AuthService(session)
                with pytest.raises(AccountLockedError):
                    await svc2.login(
                        LoginRequest(username="dave", password=PASSWORD),
                        ip="10.0.1.1",
                    )
        finally:
            tenant_id_ctx.reset(token)

    async def test_disabled_user_rejected(
        self,
        session: AsyncSession,
        tenant_a: User,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await factory.user(  # type: ignore[attr-defined]
                tenant_a,
                username="eve",
                password_hash=hash_password(PASSWORD),
                status="disabled",
            )
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = AuthService(session)
                with pytest.raises(AccountDisabledError):
                    await svc.login(
                        LoginRequest(username="eve", password=PASSWORD),
                        ip="127.0.0.1",
                    )
        finally:
            tenant_id_ctx.reset(token)
