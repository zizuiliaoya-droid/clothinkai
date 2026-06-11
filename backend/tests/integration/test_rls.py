"""集成测试：PostgreSQL Row Level Security 策略生效。

需要 PG 实例已经创建 clothing_app + clothing_bypass 角色 + 002_u01_enable_rls.py 已 apply。
本测试用 clothing_app 角色连接（启用 RLS），通过 SET LOCAL 模拟租户上下文。
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security.auth import hash_password
from app.modules.auth.models import Tenant, User

# clothing_app 角色的连接 URL（启用 RLS）
APP_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL_APP",
    "postgresql+asyncpg://clothing_app:app_password_change_me@localhost:5432/clothing_erp_test",
)

skip_if_no_rls = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL_APP"),
    reason="RLS test requires TEST_DATABASE_URL_APP env var pointing to clothing_app role",
)


@pytest.mark.integration
@pytest.mark.rls
@pytest.mark.asyncio
@skip_if_no_rls
class TestRowLevelSecurity:
    async def test_rls_filters_user_table(
        self, session: AsyncSession, tenant_a: Tenant, tenant_b: Tenant
    ) -> None:
        """绕过 ORM 钩子，直接用 RLS 角色 + SET LOCAL 验证。"""
        # 在两个租户分别插入用户（用 bypass 引擎避免被 RLS 阻挡）
        users_to_insert = [
            User(
                id=uuid4(),
                tenant_id=tenant_a.id,
                username=f"alice_{uuid4().hex[:6]}",
                password_hash=hash_password("Password123"),
            ),
            User(
                id=uuid4(),
                tenant_id=tenant_b.id,
                username=f"bob_{uuid4().hex[:6]}",
                password_hash=hash_password("Password123"),
            ),
        ]
        for u in users_to_insert:
            session.add(u)
        await session.flush()

        # 用 clothing_app 角色重新连接（RLS 启用）
        app_engine = create_async_engine(APP_DATABASE_URL, future=True)
        AppSession = async_sessionmaker(app_engine, expire_on_commit=False, class_=AsyncSession)

        try:
            async with app_engine.connect() as conn:
                async with conn.begin():
                    # 设置 tenant_a 上下文
                    await conn.execute(
                        text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": str(tenant_a.id)},
                    )
                    # 仅能看到 tenant_a 的用户
                    result = await conn.execute(
                        text(f"SELECT username FROM \"user\" WHERE id IN ({','.join(repr(str(u.id)) for u in users_to_insert)})")
                    )
                    rows = result.fetchall()
                    usernames = {row[0] for row in rows}
                    assert users_to_insert[0].username in usernames
                    assert users_to_insert[1].username not in usernames
        finally:
            await app_engine.dispose()

    async def test_bypass_rls_sees_all(
        self, session: AsyncSession, tenant_a: Tenant, tenant_b: Tenant
    ) -> None:
        """SET LOCAL app.bypass_rls = 'on' 时跨租户可见。"""
        users_to_insert = [
            User(
                id=uuid4(),
                tenant_id=tenant_a.id,
                username=f"a_{uuid4().hex[:6]}",
                password_hash=hash_password("Password123"),
            ),
            User(
                id=uuid4(),
                tenant_id=tenant_b.id,
                username=f"b_{uuid4().hex[:6]}",
                password_hash=hash_password("Password123"),
            ),
        ]
        for u in users_to_insert:
            session.add(u)
        await session.flush()

        app_engine = create_async_engine(APP_DATABASE_URL, future=True)
        try:
            async with app_engine.connect() as conn:
                async with conn.begin():
                    await conn.execute(text("SET LOCAL app.bypass_rls = 'on'"))
                    result = await conn.execute(
                        text(f"SELECT count(*) FROM \"user\" WHERE id IN ({','.join(repr(str(u.id)) for u in users_to_insert)})")
                    )
                    count = result.scalar_one()
                    assert count == 2
        finally:
            await app_engine.dispose()


@pytest.mark.integration
@pytest.mark.rls
@pytest.mark.asyncio
@skip_if_no_rls
class TestAuditLogAppendOnly:
    """audit_log REVOKE UPDATE/DELETE 验证。"""

    async def test_clothing_app_cannot_update_audit_log(
        self, session: AsyncSession
    ) -> None:
        from app.modules.auth.models import AuditLog

        # 先用 bypass 插入一条
        entry = AuditLog(actor_type="system", action="login")
        session.add(entry)
        await session.flush()
        entry_id = entry.id

        # 用 clothing_app 角色尝试 UPDATE
        app_engine = create_async_engine(APP_DATABASE_URL, future=True)
        try:
            async with app_engine.connect() as conn:
                async with conn.begin():
                    with pytest.raises(Exception):  # noqa: B017
                        await conn.execute(
                            text(
                                "UPDATE audit_log SET action = 'tampered' WHERE id = :id"
                            ),
                            {"id": entry_id},
                        )
        finally:
            await app_engine.dispose()

    async def test_clothing_app_cannot_delete_audit_log(
        self, session: AsyncSession
    ) -> None:
        from app.modules.auth.models import AuditLog

        entry = AuditLog(actor_type="system", action="login")
        session.add(entry)
        await session.flush()
        entry_id = entry.id

        app_engine = create_async_engine(APP_DATABASE_URL, future=True)
        try:
            async with app_engine.connect() as conn:
                async with conn.begin():
                    with pytest.raises(Exception):  # noqa: B017
                        await conn.execute(
                            text("DELETE FROM audit_log WHERE id = :id"),
                            {"id": entry_id},
                        )
        finally:
            await app_engine.dispose()
