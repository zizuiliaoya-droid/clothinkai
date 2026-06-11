"""U03 Blogger upsert 集成测试（FB7 边界 + 复用同一套校验/权限）."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.exceptions import FieldPermissionDenied
from app.modules.blogger.schemas import BloggerCreate
from app.modules.blogger.service import BloggerService


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpsertCreatePath:
    async def test_first_call_inserts(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            response = await svc.upsert_by_xiaohongshu_id(
                BloggerCreate(
                    xiaohongshu_id="UPSERT-001",
                    nickname="导入博主",
                    quote=Decimal("500.00"),
                ),
                user,
            )
            assert response.xiaohongshu_id == "UPSERT-001"
            assert response.quote == Decimal("500.00")
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpsertUpdatePath:
    async def test_repeated_call_updates_same_row(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            r1 = await svc.upsert_by_xiaohongshu_id(
                BloggerCreate(
                    xiaohongshu_id="UPSERT-002",
                    nickname="原昵称",
                    quote=Decimal("100.00"),
                ),
                user,
            )
            r2 = await svc.upsert_by_xiaohongshu_id(
                BloggerCreate(
                    xiaohongshu_id="UPSERT-002",
                    nickname="新昵称",
                    quote=Decimal("200.00"),
                ),
                user,
            )
            # 同一 blogger id（数据库原子 upsert）
            assert r1.id == r2.id
            assert r2.nickname == "新昵称"
            assert r2.quote == Decimal("200.00")
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpsertReusesValidation:
    """FB7：upsert 必须复用同一套校验/权限/审计."""

    async def test_designer_field_permission_denied(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        designer_role: Any,
    ) -> None:
        """designer 角色无权写 quote，即使是 import 路径也被拒."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[designer_role])
            svc = BloggerService(session)
            with pytest.raises(FieldPermissionDenied):
                await svc.upsert_by_xiaohongshu_id(
                    BloggerCreate(
                        xiaohongshu_id="UPSERT-Y",
                        nickname="x",
                        quote=Decimal("100.00"),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_finance_quote_write_denied(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
    ) -> None:
        """finance 可读 quote 但不可写，import 也被拒."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BloggerService(session)
            with pytest.raises(FieldPermissionDenied):
                await svc.upsert_by_xiaohongshu_id(
                    BloggerCreate(
                        xiaohongshu_id="UPSERT-Z",
                        nickname="x",
                        quote=Decimal("100.00"),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)
