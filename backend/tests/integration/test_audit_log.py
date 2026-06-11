"""集成测试：EP01-S08 审计日志 + BR-AUDIT-002 append-only 约束。"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.tenancy import tenant_id_ctx, user_id_ctx
from app.modules.auth.models import AuditLog, Tenant


@pytest.mark.integration
@pytest.mark.asyncio
class TestAuditLog:
    async def test_log_writes_record(
        self, session: AsyncSession, tenant_a: Tenant
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            audit = AuditService(session)
            await audit.log(
                action="login",
                actor_type="user",
                resource="user",
                resource_id="1",
                ip="127.0.0.1",
            )
            await session.flush()

            stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_a.id)
            entries = (await session.execute(stmt)).scalars().all()
            assert len(entries) == 1
            assert entries[0].action == "login"
            assert entries[0].actor_type == "user"
            assert entries[0].ip == "127.0.0.1"
        finally:
            tenant_id_ctx.reset(token)

    async def test_log_uses_context_when_actor_not_provided(
        self, session: AsyncSession, tenant_a: Tenant, factory: object
    ) -> None:
        """从 contextvars 读取 user_id / tenant_id / actor_type。"""
        from app.core.tenancy import actor_type_ctx

        user = await factory.user(tenant_a)  # type: ignore[attr-defined]

        t_token = tenant_id_ctx.set(tenant_a.id)
        u_token = user_id_ctx.set(user.id)
        a_token = actor_type_ctx.set("user")
        try:
            audit = AuditService(session)
            await audit.log(action="user_create")
            await session.flush()

            stmt = select(AuditLog).where(AuditLog.action == "user_create")
            entry = (await session.execute(stmt)).scalar_one()
            assert entry.tenant_id == tenant_a.id
            assert entry.user_id == user.id
            assert entry.actor_type == "user"
        finally:
            tenant_id_ctx.reset(t_token)
            user_id_ctx.reset(u_token)
            actor_type_ctx.reset(a_token)

    async def test_query_filters_combined(
        self, session: AsyncSession, tenant_a: Tenant
    ) -> None:
        from app.modules.auth.repository import AuditLogRepository

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            audit = AuditService(session)
            for action in ("login", "login_failed", "user_create", "login"):
                await audit.log(action=action, actor_type="user", resource="user")
            await session.flush()

            repo = AuditLogRepository(session)
            items, total = await repo.query(action="login")
            assert total == 2
            for item in items:
                assert item.action == "login"
        finally:
            tenant_id_ctx.reset(token)
