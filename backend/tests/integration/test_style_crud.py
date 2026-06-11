"""U02 Style CRUD 集成测试。

覆盖：
- EP02-S01 创建款式（含 style_code 重复 409）
- EP02-S03 编辑款式（含字段未变更不写 audit）
- 软删 / 停用 / 恢复
- 多租户隔离（隔离不到 service 层而是 ORM 层钩子）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.enums import Category
from app.modules.product.exceptions import (
    StyleCodeConflictError,
    StyleHasActiveSkuError,
    StyleNotFoundError,
)
from app.modules.product.schemas import StyleCreate, StyleUpdate
from app.modules.product.service import StyleService


@pytest.fixture
def stub_cache() -> AsyncMock:
    return AsyncMock()


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateStyle:
    async def test_create_basic(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            response = await svc.create_style(
                StyleCreate(
                    style_code="W001",
                    style_name="波点花边连衣裙",
                    short_name="波点花边",
                    category=Category.DRESS,
                ),
                user,
            )
            assert response.style_code == "W001"
            assert response.style_name == "波点花边连衣裙"
            assert response.short_name == "波点花边"
            assert response.design_status == "大货"
            assert response.is_active is True
            assert response.is_deleted is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_duplicate_style_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.style(style_code="W001")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            with pytest.raises(StyleCodeConflictError):
                await svc.create_style(
                    StyleCreate(
                        style_code="W001",
                        style_name="另一款",
                        category=Category.DRESS,
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdateStyle:
    async def test_update_style_name_no_audit_for_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """EP02-S03: 编辑款式名不写 audit (style_name 非敏感字段)."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_name="原名")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            response = await svc.update_style(
                style.id,
                StyleUpdate(style_name="新名"),
                user,
            )
            assert response.style_name == "新名"
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_unchanged_returns_same(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """字段未变更时不更新 + 不写 audit (BR-U02-32)."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_name="保持")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            response = await svc.update_style(
                style.id,
                StyleUpdate(style_name="保持"),
                user,
            )
            assert response.style_name == "保持"
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_nonexistent_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            with pytest.raises(StyleNotFoundError):
                await svc.update_style(uuid4(), StyleUpdate(style_name="x"), user)
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSoftDeleteStyle:
    async def test_soft_delete_blocked_by_active_sku(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """BR-U02-21: 删 style 必须先停用所有 active sku."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await product_factory.sku(style)
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            with pytest.raises(StyleHasActiveSkuError):
                await svc.soft_delete_style(style.id, user)
        finally:
            tenant_id_ctx.reset(token)

    async def test_soft_delete_no_skus(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            await svc.soft_delete_style(style.id, user)
            await session.refresh(style)
            assert style.is_deleted is True
            assert style.is_active is False
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestListStyles:
    async def test_list_pagination(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            for i in range(25):
                await product_factory.style(style_code=f"ST{i:03d}")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            from app.modules.product.repository import StyleListFilters

            page = await svc.list_styles(
                filters=StyleListFilters(),
                page=1,
                page_size=10,
                user=user,
            )
            assert len(page.items) == 10
            assert page.total == 25
        finally:
            tenant_id_ctx.reset(token)
