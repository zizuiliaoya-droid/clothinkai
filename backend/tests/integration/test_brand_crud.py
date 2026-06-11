"""U02 Brand 字典 CRUD 集成测试."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.brand_schemas import BrandCreate, BrandUpdate
from app.modules.product.brand_service import BrandService
from app.modules.product.exceptions import (
    BrandCodeConflictError,
    BrandNotFoundError,
)


@pytest.mark.integration
@pytest.mark.asyncio
class TestBrandCrud:
    async def test_create_brand(
        self,
        session: AsyncSession,
        tenant_a: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = BrandService(session)
            response = await svc.create_brand(
                BrandCreate(brand_code="NIKE", brand_name="耐克")
            )
            assert response.brand_code == "NIKE"
            assert response.is_active is True
        finally:
            tenant_id_ctx.reset(token)

    async def test_duplicate_brand_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.brand(brand_code="NIKE")
            svc = BrandService(session)
            with pytest.raises(BrandCodeConflictError):
                await svc.create_brand(
                    BrandCreate(brand_code="NIKE", brand_name="另一个")
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_disable_brand(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            brand = await product_factory.brand(is_active=True)
            svc = BrandService(session)
            response = await svc.disable_brand(brand.id)
            assert response.is_active is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_get_nonexistent_raises(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = BrandService(session)
            with pytest.raises(BrandNotFoundError):
                await svc.get_brand(uuid4())
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_brand_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            brand = await product_factory.brand(brand_name="原名")
            svc = BrandService(session)
            response = await svc.update_brand(
                brand.id, BrandUpdate(brand_name="新名")
            )
            assert response.brand_name == "新名"
        finally:
            tenant_id_ctx.reset(token)
