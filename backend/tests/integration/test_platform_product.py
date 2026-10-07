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
            svc = PlatformProductService(session)
            resp = await svc.create(
                PlatformProductCreate(platform="qianniu", platform_id="123456", style_id=style.id),
                user.id,
            )
            assert resp.platform == "qianniu"
            assert resp.platform_id == "123456"
            assert resp.style_id == style.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_duplicate_409(
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
            svc = PlatformProductService(session)
            await svc.create(
                PlatformProductCreate(platform="qianniu", platform_id="DUP1", style_id=style.id),
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
            style2 = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            pp1 = await svc.create_or_update(
                platform="taobao",
                platform_id="T1",
                style_id=style.id,
                user_id=user.id,
            )
            pp2 = await svc.create_or_update(
                platform="taobao",
                platform_id="T1",
                style_id=style2.id,
                title="新标题",
                user_id=user.id,
            )
            assert pp1.id == pp2.id  # 同行 upsert
            assert pp2.style_id == style2.id
            assert pp2.title == "新标题"
        finally:
            tenant_id_ctx.reset(token)

    async def test_find_hit_and_miss(
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
            svc = PlatformProductService(session)
            await svc.create(
                PlatformProductCreate(platform="douyin", platform_id="D1", style_id=style.id),
                user.id,
            )
            assert await svc.find_by_platform_id("douyin", "D1") is not None
            assert await svc.find_by_platform_id("douyin", "MISSING") is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_invalid_style_422(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)
            with pytest.raises(ValidationError):
                await svc.create(
                    PlatformProductCreate(platform="qianniu", platform_id="X1", style_id=uuid4()),
                    user.id,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_delete(
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
            svc = PlatformProductService(session)
            resp = await svc.create(
                PlatformProductCreate(platform="qianniu", platform_id="DEL1", style_id=style.id),
                user.id,
            )
            await svc.delete(resp.id, user.id)
            assert await svc.find_by_platform_id("qianniu", "DEL1") is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_response_carries_goods_short_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """平台链接页靠商品名认链接：单条返回与列表两条读取路径都要带上简称。"""
        from app.modules.product.goods_models import GoodsMain, GoodsStyleItem

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            goods_code = f"G{uuid4().hex[:8]}"
            goods = GoodsMain(
                tenant_id=tenant_a.id,
                goods_code=goods_code,
                goods_title="很长的商品全称",
                short_name="短名",
            )
            session.add(goods)
            await session.flush()
            session.add(
                GoodsStyleItem(tenant_id=tenant_a.id, goods_main_id=goods.id, style_id=style.id)
            )
            await session.flush()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PlatformProductService(session)

            created = await svc.create(
                PlatformProductCreate(platform="qianniu", platform_id="SN1", style_id=style.id),
                user.id,
            )
            assert created.goods_main_id == goods.id
            assert created.goods_short_name == "短名"
            assert created.goods_title == "很长的商品全称"
            assert created.goods_is_suit is False
            assert created.style_code == style.style_code

            items, total = await svc.list_detailed(tenant_id=tenant_a.id, goods_main_id=goods.id)
            assert total == 1
            assert items[0].goods_short_name == "短名"
            assert items[0].goods_title == "很长的商品全称"

            # AC 62（补充 2）：界面不显示商品编码，但平台链接列表按商品编码搜仍命中
            items, total = await svc.list_detailed(tenant_id=tenant_a.id, keyword=goods_code)
            assert total == 1
            assert items[0].platform_id == "SN1"
        finally:
            tenant_id_ctx.reset(token)
