"""集成测试：EP01-S02 修改密码（BR-PWD-001/002 + BR-TKN-004 token 失效）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidCredentialsError
from app.core.security.auth import hash_password, verify_password
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.exceptions import WeakPasswordError
from app.modules.auth.models import RefreshToken, User
from app.modules.auth.schemas import ChangePasswordRequest
from app.modules.auth.service import AuthService

OLD_PASSWORD = "OldPass123"
NEW_PASSWORD = "NewPass456"


@pytest.fixture
def stub_cache() -> AsyncMock:
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.delete = AsyncMock(return_value=0)
    mock.setex = AsyncMock()
    mock.exists = AsyncMock(return_value=False)
    mock.expire = AsyncMock(return_value=True)
    mock.incr = AsyncMock(return_value=1)
    mock.ttl = AsyncMock(return_value=-2)
    return mock


@pytest.mark.integration
@pytest.mark.asyncio
class TestChangePassword:
    async def test_change_success(
        self, session: AsyncSession, tenant_a: User, factory: object, stub_cache: AsyncMock
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(  # type: ignore[attr-defined]
                tenant_a, password_hash=hash_password(OLD_PASSWORD)
            )
            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                svc = AuthService(session)
                await svc.change_password(user.id, OLD_PASSWORD, NEW_PASSWORD)

            await session.refresh(user)
            assert verify_password(NEW_PASSWORD, user.password_hash)
            assert verify_password(OLD_PASSWORD, user.password_hash) is False
            assert user.password_must_change is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_wrong_old_password(
        self, session: AsyncSession, tenant_a: User, factory: object, stub_cache: AsyncMock
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(  # type: ignore[attr-defined]
                tenant_a, password_hash=hash_password(OLD_PASSWORD)
            )
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = AuthService(session)
                with pytest.raises(InvalidCredentialsError):
                    await svc.change_password(user.id, "WrongOld123", NEW_PASSWORD)
        finally:
            tenant_id_ctx.reset(token)

    async def test_same_as_current(
        self, session: AsyncSession, tenant_a: User, factory: object, stub_cache: AsyncMock
    ) -> None:
        """新密码与当前密码相同 → 拒绝。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(  # type: ignore[attr-defined]
                tenant_a, password_hash=hash_password(OLD_PASSWORD)
            )
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = AuthService(session)
                with pytest.raises(InvalidCredentialsError):
                    await svc.change_password(user.id, OLD_PASSWORD, OLD_PASSWORD)
        finally:
            tenant_id_ctx.reset(token)

    async def test_weak_password_rejected_at_schema_layer(self) -> None:
        """Schema 层就阻挡弱密码（避免到 service）。"""
        with pytest.raises(WeakPasswordError):
            ChangePasswordRequest(old_password="x", new_password="weak")

    async def test_pwd_iat_updated_after_change(
        self, session: AsyncSession, tenant_a: User, factory: object, stub_cache: AsyncMock
    ) -> None:
        """安全戳 password_changed_at 必须前进（BR-TKN-004）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(  # type: ignore[attr-defined]
                tenant_a, password_hash=hash_password(OLD_PASSWORD)
            )
            old_iat = user.password_changed_at
            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                svc = AuthService(session)
                await svc.change_password(user.id, OLD_PASSWORD, NEW_PASSWORD)
            await session.refresh(user)
            assert user.password_changed_at > old_iat
        finally:
            tenant_id_ctx.reset(token)

    async def test_refresh_tokens_revoked_after_change(
        self, session: AsyncSession, tenant_a: User, factory: object, stub_cache: AsyncMock
    ) -> None:
        """所有现有 refresh_token 应被吊销。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(  # type: ignore[attr-defined]
                tenant_a, password_hash=hash_password(OLD_PASSWORD)
            )
            # 手工塞一个未吊销的 refresh_token
            from datetime import datetime, timedelta, timezone
            from uuid import uuid4

            session.add(
                RefreshToken(
                    tenant_id=tenant_a.id,
                    user_id=user.id,
                    jti=uuid4().hex,
                    issued_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                )
            )
            await session.flush()

            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                svc = AuthService(session)
                await svc.change_password(user.id, OLD_PASSWORD, NEW_PASSWORD)

            stmt = select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
            unrevoked = (await session.execute(stmt)).scalars().all()
            assert len(unrevoked) == 0
        finally:
            tenant_id_ctx.reset(token)
