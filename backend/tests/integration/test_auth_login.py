"""集成测试：EP01-S01 登录主路径 + 失败 + 限流 + 锁定（BR-AUTH-001/002）。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    InvalidCredentialsError,
    RateLimitedError,
)
from app.core.security.auth import hash_password
from app.core.tenancy import bypass_rls_ctx, tenant_id_ctx
from app.modules.auth.models import AuditLog, Tenant, User
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
            with (
                patch("app.modules.auth.service.cache", stub_cache),
                patch("app.core.security.permissions.cache", stub_cache),
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

    async def test_login_unknown_user(self, session: AsyncSession, stub_cache: AsyncMock) -> None:
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


# ---------------------------------------------------------------------------
# 7b：登录类审计补齐账号与租户
# ---------------------------------------------------------------------------

WRONG_PASSWORD = "WrongPwd123"


@contextmanager
def _login_ctx(stale_tenant_id: UUID | None = None) -> Iterator[None]:
    """模拟生产登录请求的上下文。

    - ``bypass_rls_ctx=True``：与 ``get_bypass_session`` 一致，ORM 租户过滤与 RLS 都不生效
    - ``tenant_id_ctx``：TenancyContextMiddleware 从请求里（未验签的）旧 token 解出的租户；
      请求没带 token 时为 None
    """
    bypass_token = bypass_rls_ctx.set(True)
    tenant_token = tenant_id_ctx.set(stale_tenant_id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(tenant_token)
        bypass_rls_ctx.reset(bypass_token)


def _uname() -> str:
    # bypass 下按用户名跨租户查人：固定用户名会撞上库里别处提交过的数据
    return f"aud_{uuid4().hex[:10]}"


async def _audit_rows(session: AsyncSession, action: str, username: str) -> list[AuditLog]:
    stmt = select(AuditLog).where(
        AuditLog.action == action,
        AuditLog.after["username"].astext == username,
    )
    return list((await session.execute(stmt)).scalars().all())


async def _default_tenant_id(session: AsyncSession) -> UUID:
    stmt = select(Tenant.id).where(Tenant.code == "default")
    return (await session.execute(stmt)).scalar_one()


@pytest.mark.integration
@pytest.mark.asyncio
class TestLoginAuditAccountAndTenant:
    """6 个登录类审计动作都写 ``after.username`` 与显式 ``tenant_id``。

    同时确认登录判定、返回码、L3 / L4 计数没变：每个用例都断言异常类型、status_code 与 code。
    """

    @pytest.fixture(autouse=True)
    def _patch_cache(self, stub_cache: AsyncMock) -> Iterator[None]:
        with (
            patch("app.modules.auth.service.cache", stub_cache),
            patch("app.core.security.permissions.cache", stub_cache),
        ):
            yield

    async def test_success_records_username_and_user_tenant(
        self, session: AsyncSession, tenant_a: Tenant, factory: object
    ) -> None:
        uname = _uname()
        user = await factory.user(  # type: ignore[attr-defined]
            tenant_a, username=uname, password_hash=hash_password(PASSWORD)
        )
        with _login_ctx():
            access, refresh, ret_user, must_change = await AuthService(session).login(
                LoginRequest(username=uname, password=PASSWORD), ip="10.0.0.1", user_agent="pytest"
            )
        assert access
        assert refresh
        assert ret_user.id == user.id
        assert must_change is False

        [row] = await _audit_rows(session, "login", uname)
        assert row.after == {"username": uname}
        assert row.tenant_id == tenant_a.id
        assert row.user_id == user.id
        assert row.ip == "10.0.0.1"

    async def test_unknown_user_records_default_tenant(self, session: AsyncSession) -> None:
        uname = _uname()
        default_id = await _default_tenant_id(session)
        with _login_ctx(), pytest.raises(InvalidCredentialsError) as exc_info:
            await AuthService(session).login(
                LoginRequest(username=uname, password="x"), ip="10.0.0.2"
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "INVALID_CREDENTIALS"

        [row] = await _audit_rows(session, "login_failed", uname)
        assert row.tenant_id == default_id
        assert row.actor_type == "unknown"
        assert row.user_id is None

    async def test_wrong_password_records_user_tenant(
        self, session: AsyncSession, tenant_a: Tenant, factory: object
    ) -> None:
        uname = _uname()
        user = await factory.user(tenant_a, username=uname)  # type: ignore[attr-defined]
        with _login_ctx(), pytest.raises(InvalidCredentialsError) as exc_info:
            await AuthService(session).login(
                LoginRequest(username=uname, password=WRONG_PASSWORD), ip="10.0.0.3"
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "INVALID_CREDENTIALS"

        [row] = await _audit_rows(session, "login_failed", uname)
        assert row.tenant_id == tenant_a.id
        assert row.user_id == user.id
        assert row.actor_type == "user"

    async def test_locked_account_records_username_and_tenant(
        self, session: AsyncSession, tenant_a: Tenant, factory: object
    ) -> None:
        uname = _uname()
        user = await factory.user(tenant_a, username=uname)  # type: ignore[attr-defined]
        user.locked_at = datetime.now(UTC)
        await session.flush()
        with _login_ctx(), pytest.raises(AccountLockedError) as exc_info:
            await AuthService(session).login(
                LoginRequest(username=uname, password=PASSWORD), ip="10.0.0.4"
            )
        assert exc_info.value.status_code == 423
        assert exc_info.value.code == "ACCOUNT_LOCKED"

        [row] = await _audit_rows(session, "login_locked", uname)
        assert row.tenant_id == tenant_a.id
        assert row.user_id == user.id

    async def test_disabled_account_records_username_and_tenant(
        self, session: AsyncSession, tenant_a: Tenant, factory: object
    ) -> None:
        uname = _uname()
        user = await factory.user(  # type: ignore[attr-defined]
            tenant_a, username=uname, status="disabled"
        )
        with _login_ctx(), pytest.raises(AccountDisabledError) as exc_info:
            await AuthService(session).login(
                LoginRequest(username=uname, password=PASSWORD), ip="10.0.0.5"
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.code == "ACCOUNT_DISABLED"

        [row] = await _audit_rows(session, "login_disabled", uname)
        assert row.tenant_id == tenant_a.id
        assert row.user_id == user.id

    async def test_account_lock_records_username_and_tenant(
        self, session: AsyncSession, tenant_a: Tenant, factory: object
    ) -> None:
        """跨 10 个 IP 各错 1 次（绕开 L3）→ 第 10 次仍是 401，同时锁账户并写 1 条 user_lock。"""
        uname = _uname()
        user = await factory.user(tenant_a, username=uname)  # type: ignore[attr-defined]
        svc = AuthService(session)
        with _login_ctx():
            for i in range(10):
                with pytest.raises(InvalidCredentialsError):
                    await svc.login(
                        LoginRequest(username=uname, password=WRONG_PASSWORD), ip=f"10.0.0.{i}"
                    )
        await session.refresh(user)
        assert user.locked_at is not None
        assert user.failed_login_count == 10

        [lock_row] = await _audit_rows(session, "user_lock", uname)
        assert lock_row.tenant_id == tenant_a.id
        assert lock_row.actor_type == "system"
        failed_rows = await _audit_rows(session, "login_failed", uname)
        assert len(failed_rows) == 10
        assert {r.tenant_id for r in failed_rows} == {tenant_a.id}

    async def test_rate_limited_records_username_and_user_tenant(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        """同一 IP 错 5 次 → 第 6 次（密码对也一样）429；被限流那次不计数。"""
        uname = _uname()
        user = await factory.user(tenant_a, username=uname)  # type: ignore[attr-defined]
        ip = "10.0.1.1"
        svc = AuthService(session)
        with _login_ctx():
            for _ in range(5):
                with pytest.raises(InvalidCredentialsError):
                    await svc.login(LoginRequest(username=uname, password=WRONG_PASSWORD), ip=ip)
            with pytest.raises(RateLimitedError) as exc_info:
                await svc.login(LoginRequest(username=uname, password=PASSWORD), ip=ip)
        assert exc_info.value.status_code == 429
        assert exc_info.value.code == "RATE_LIMITED"
        assert exc_info.value.details["retry_after_seconds"] == 900

        [row] = await _audit_rows(session, "login_rate_limited", uname)
        assert row.tenant_id == tenant_a.id
        assert row.actor_type == "unknown"
        # L3 计数仍是 5、L4 计数仍是 5：限流分支只多查了一次用户，没有计数
        assert await stub_cache.get(f"login:fail:{ip}:{uname}") == "5"
        await session.refresh(user)
        assert user.failed_login_count == 5

    async def test_rate_limited_unknown_user_records_default_tenant(
        self, session: AsyncSession, tenant_a: Tenant
    ) -> None:
        uname = _uname()
        default_id = await _default_tenant_id(session)
        assert default_id != tenant_a.id  # 场景有效性：两个租户真不同
        ip = "10.0.1.2"
        svc = AuthService(session)
        with _login_ctx():
            for _ in range(5):
                with pytest.raises(InvalidCredentialsError):
                    await svc.login(LoginRequest(username=uname, password="x"), ip=ip)
            with pytest.raises(RateLimitedError):
                await svc.login(LoginRequest(username=uname, password="x"), ip=ip)

        [row] = await _audit_rows(session, "login_rate_limited", uname)
        assert row.tenant_id == default_id

    async def test_stale_token_context_does_not_mislabel_tenant(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        tenant_b: Tenant,
        factory: object,
    ) -> None:
        """请求带着 tenant_b 的旧 token（中间件不验签就写进上下文）：审计不能记成 tenant_b。"""
        uname = _uname()
        ghost = _uname()
        await factory.user(  # type: ignore[attr-defined]
            tenant_a, username=uname, password_hash=hash_password(PASSWORD)
        )
        default_id = await _default_tenant_id(session)
        assert default_id not in {tenant_a.id, tenant_b.id}
        svc = AuthService(session)
        with _login_ctx(stale_tenant_id=tenant_b.id):
            await svc.login(LoginRequest(username=uname, password=PASSWORD), ip="10.0.2.1")
            with pytest.raises(InvalidCredentialsError):
                await svc.login(LoginRequest(username=ghost, password="x"), ip="10.0.2.1")

        [ok_row] = await _audit_rows(session, "login", uname)
        assert ok_row.tenant_id == tenant_a.id
        [ghost_row] = await _audit_rows(session, "login_failed", ghost)
        assert ghost_row.tenant_id == default_id

    async def test_username_truncated_to_64_chars(
        self, session: AsyncSession, stub_cache: AsyncMock
    ) -> None:
        # model_construct 绕过 schema 的 64 字限制，验证 service 层的截断兜底
        long_name = _uname() * 6
        assert len(long_name) > 64
        ip = "10.0.3.1"
        with _login_ctx(), pytest.raises(InvalidCredentialsError):
            await AuthService(session).login(
                LoginRequest.model_construct(username=long_name, password="x"), ip=ip
            )

        [row] = await _audit_rows(session, "login_failed", long_name[:64])
        assert row.after == {"username": long_name[:64]}
        # 限流计数仍按完整用户名
        assert await stub_cache.get(f"login:fail:{ip}:{long_name}") == "1"
