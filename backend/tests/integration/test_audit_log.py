"""集成测试：EP01-S08 审计日志 + BR-AUDIT-002 append-only 约束。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.tenancy import tenant_id_ctx, user_id_ctx
from app.modules.auth.models import AuditLog, Tenant


@pytest.mark.integration
@pytest.mark.asyncio
class TestAuditLog:
    async def test_log_writes_record(self, session: AsyncSession, tenant_a: Tenant) -> None:
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

    async def test_explicit_tenant_id_overrides_context(
        self, session: AsyncSession, tenant_a: Tenant, tenant_b: Tenant
    ) -> None:
        """显式 tenant_id 优先于上下文（登录请求的上下文可能来自未验签的旧 token）。"""
        marker = uuid4().hex
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await AuditService(session).log(
                action="explicit_tenant_probe", resource_id=marker, tenant_id=tenant_b.id
            )
            await session.flush()
        finally:
            tenant_id_ctx.reset(token)

        stmt = select(AuditLog).where(
            AuditLog.action == "explicit_tenant_probe", AuditLog.resource_id == marker
        )
        entry = (await session.execute(stmt)).scalar_one()
        assert entry.tenant_id == tenant_b.id

    async def test_explicit_tenant_id_without_context(
        self, session: AsyncSession, tenant_a: Tenant
    ) -> None:
        marker = uuid4().hex
        assert tenant_id_ctx.get() is None
        await AuditService(session).log(
            action="explicit_tenant_probe", resource_id=marker, tenant_id=tenant_a.id
        )
        await session.flush()

        stmt = select(AuditLog).where(
            AuditLog.action == "explicit_tenant_probe", AuditLog.resource_id == marker
        )
        entry = (await session.execute(stmt)).scalar_one()
        assert entry.tenant_id == tenant_a.id

    async def test_query_filters_combined(self, session: AsyncSession, tenant_a: Tenant) -> None:
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
