"""U10b 集成测试：平台商品映射 CRUD + 幂等 + 反查 + 引用校验。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.product.platform_product_schemas import PlatformProductCreate
from app.modules.product.platform_product_service import (
    PlatformProductConflictError,
    PlatformProductService,
)


@pytest.mark.integration
@pytest.mark.asyncio
class TestPlatformProduct:
    async def test_create_success(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        admin_role: Any, product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            resp = await svc.create(
                PlatformProductCreate(
                    platform="qianniu", platform_id="123456", style_id=style.id
                ),
                user.id,
            )
            assert resp.platform == "qianniu"
            assert resp.platform_id == "123456"
            assert resp.style_id == style.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_duplicate_409(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        admin_role: Any, product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            await svc.create(
                PlatformProductCreate(
                    platform="qianniu", platform_id="DUP1", style_id=style.id
                ),
                user.id,
            )
            with pytest.raises(PlatformProductConflictError):
                await svc.create(
                    PlatformProductCreate(
                        platform="qianniu", platform_id="DUP1", style_id=style.id
                    ),
                    user.id,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_or_update_idempotent(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        admin_role: Any, product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            style2 = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            pp1 = await svc.create_or_update(
                platform="taobao", platform_id="T1",
                style_id=style.id, user_id=user.id,
            )
            pp2 = await svc.create_or_update(
                platform="taobao", platform_id="T1",
                style_id=style2.id, title="新标题", user_id=user.id,
            )
            assert pp1.id == pp2.id  # 同行 upsert
            assert pp2.style_id == style2.id
            assert pp2.title == "新标题"
        finally:
            tenant_id_ctx.reset(token)

    async def test_find_hit_and_miss(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        admin_role: Any, product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            await svc.create(
                PlatformProductCreate(
                    platform="douyin", platform_id="D1", style_id=style.id
                ),
                user.id,
            )
            assert await svc.find_by_platform_id("douyin", "D1") is not None
            assert await svc.find_by_platform_id("douyin", "MISSING") is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_invalid_style_422(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            with pytest.raises(ValidationError):
                await svc.create(
                    PlatformProductCreate(
                        platform="qianniu", platform_id="X1", style_id=uuid4()
                    ),
                    user.id,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_delete(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        admin_role: Any, product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            resp = await svc.create(
                PlatformProductCreate(
                    platform="qianniu", platform_id="DEL1", style_id=style.id
                ),
                user.id,
            )
            await svc.delete(resp.id, user.id)
            assert await svc.find_by_platform_id("qianniu", "DEL1") is None
        finally:
            tenant_id_ctx.reset(token)
