"""集成测试：EP01-S07 多租户隔离 + EP10-NFR03。

按 NFR Requirements Q13=B：典型实体（user）有租户隔离测试，其他靠基类继承。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    TenantContextMismatchError,
    TenantContextMissingError,
)
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Tenant, User


@pytest.mark.integration
@pytest.mark.tenant
@pytest.mark.asyncio
class TestTenantIsolation:
    async def test_orm_query_filters_by_tenant(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        tenant_b: Tenant,
        factory: object,
    ) -> None:
        """SELECT 自动 WHERE tenant_id = ctx。"""
        # 在 A 租户创建 user
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await factory.user(tenant_a, username="alice_in_a")  # type: ignore[attr-defined]
        finally:
            tenant_id_ctx.reset(token)

        # 在 B 租户创建 user
        token = tenant_id_ctx.set(tenant_b.id)
        try:
            await factory.user(tenant_b, username="bob_in_b")  # type: ignore[attr-defined]
        finally:
            tenant_id_ctx.reset(token)

        # 切回 A 上下文查询：只能看到 alice
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            users = (await session.execute(select(User))).scalars().all()
            usernames = {u.username for u in users}
            assert "alice_in_a" in usernames
            assert "bob_in_b" not in usernames
        finally:
            tenant_id_ctx.reset(token)

    async def test_insert_auto_fills_tenant_id(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
    ) -> None:
        """INSERT 时未填 tenant_id，自动从 context 填充。"""
        from app.core.security.auth import hash_password
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            # 不显式设置 tenant_id
            user = User(
                id=uuid4(),
                username=f"auto_{uuid4().hex[:6]}",
                password_hash=hash_password("Password123"),
                tenant_id=None,  # type: ignore[arg-type]
            )
            session.add(user)
            await session.flush()
            assert user.tenant_id == tenant_a.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_insert_with_mismatched_tenant_raises(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        tenant_b: Tenant,
    ) -> None:
        """显式 tenant_id 与 ctx 不匹配 → 抛 TenantContextMismatchError。"""
        from app.core.security.auth import hash_password
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = User(
                id=uuid4(),
                tenant_id=tenant_b.id,  # 故意错配
                username=f"mismatch_{uuid4().hex[:6]}",
                password_hash=hash_password("Password123"),
            )
            session.add(user)
            with pytest.raises(TenantContextMismatchError):
                await session.flush()
        finally:
            tenant_id_ctx.reset(token)

    async def test_unique_constraint_per_tenant(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        tenant_b: Tenant,
        factory: object,
    ) -> None:
        """两个租户可以有同名 username（约束 (tenant_id, username) 唯一）。"""
        # A 租户创建 alice
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await factory.user(tenant_a, username="alice")  # type: ignore[attr-defined]
        finally:
            tenant_id_ctx.reset(token)

        # B 租户也能创建 alice
        token = tenant_id_ctx.set(tenant_b.id)
        try:
            user_b = await factory.user(tenant_b, username="alice")  # type: ignore[attr-defined]
            assert user_b.tenant_id == tenant_b.id
        finally:
            tenant_id_ctx.reset(token)
