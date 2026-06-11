"""U02 SKU CRUD 集成测试 + 字段权限矩阵。

覆盖：
- EP02-S02 创建 SKU（style_id 不存在 / sku_code 重复）
- EP02-S04 编辑 cost_price（不同角色权限矩阵）
- EP02-S05 按款式查询 SKU
- 软删 + 引用检查
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.enums import SourcingType
from app.modules.product.exceptions import (
    FieldPermissionDenied,
    InvalidStyleReferenceError,
    SkuCodeConflictError,
    StyleNotFoundError,
)
from app.modules.product.schemas import SkuCreate, SkuUpdate
from app.modules.product.service import SkuService


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateSku:
    async def test_create_basic(
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
            svc = SkuService(session)
            response = await svc.create_sku(
                SkuCreate(
                    style_id=style.id,
                    sku_code="W001-R-M",
                    color="红",
                    size="M",
                    cost_price=Decimal("100.00"),
                    sourcing_type=SourcingType.SELF_PRODUCED,
                ),
                user,
            )
            assert response.sku_code == "W001-R-M"
            assert response.cost_price == Decimal("100.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_invalid_style_id(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)
            with pytest.raises(InvalidStyleReferenceError):
                await svc.create_sku(
                    SkuCreate(
                        style_id=uuid4(),  # 不存在
                        sku_code="X-R-M",
                        color="红",
                        size="M",
                        cost_price=Decimal("100.00"),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_duplicate_sku_code(
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
            await product_factory.sku(style, sku_code="DUP")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)
            with pytest.raises(SkuCodeConflictError):
                await svc.create_sku(
                    SkuCreate(
                        style_id=style.id,
                        sku_code="DUP",
                        color="蓝",
                        size="L",
                        cost_price=Decimal("90.00"),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdateSkuFieldPermission:
    """EP02-S04 字段权限矩阵."""

    async def test_merchandiser_can_write_cost_price(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        follower_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            sku = await product_factory.sku(style, cost_price=Decimal("100.00"))
            user = await factory.user(tenant_a, roles=[follower_role])
            svc = SkuService(session)
            response = await svc.update_sku(
                sku.id,
                SkuUpdate(cost_price=Decimal("120.00")),
                user,
            )
            assert response.cost_price == Decimal("120.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_designer_cannot_write_cost_price(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        designer_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            sku = await product_factory.sku(style, cost_price=Decimal("100.00"))
            user = await factory.user(tenant_a, roles=[designer_role])
            svc = SkuService(session)
            with pytest.raises(FieldPermissionDenied):
                await svc.update_sku(
                    sku.id,
                    SkuUpdate(cost_price=Decimal("120.00")),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_pr_cannot_see_cost_price_in_response(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
    ) -> None:
        """PR 角色 GET sku 时 cost_price 字段被过滤为 None."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            sku = await product_factory.sku(
                style, cost_price=Decimal("100.00")
            )
            user = await factory.user(tenant_a, roles=[pr_role])
            svc = SkuService(session)
            response = await svc.get_sku(sku.id, user)
            assert response.cost_price is None
            assert response.purchase_price is None
            # base_price 仍可见
            assert response.base_price is not None

        finally:
            tenant_id_ctx.reset(token)

    async def test_finance_can_see_cost_price(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            sku = await product_factory.sku(
                style, cost_price=Decimal("123.45")
            )
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = SkuService(session)
            response = await svc.get_sku(sku.id, user)
            assert response.cost_price == Decimal("123.45")
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestListByStyle:
    async def test_list_returns_skus(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """EP02-S05: 6 SKU 全返回."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            for i, color in enumerate(["红", "蓝", "黑"]):
                for size in ["S", "M"]:
                    await product_factory.sku(
                        style, sku_code=f"K-{color}-{size}-{i}",
                        color=color, size=size,
                    )
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)
            items = await svc.list_by_style(
                style.id, include_inactive=False, user=user
            )
            assert len(items) == 6
        finally:
            tenant_id_ctx.reset(token)

    async def test_list_empty_returns_empty(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """EP02-S05: 0 SKU 返回空 200."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)
            items = await svc.list_by_style(
                style.id, include_inactive=False, user=user
            )
            assert items == []
        finally:
            tenant_id_ctx.reset(token)

    async def test_list_unknown_style_raises_404(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)
            with pytest.raises(StyleNotFoundError):
                await svc.list_by_style(uuid4(), include_inactive=False, user=user)
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSoftDeleteSku:
    async def test_soft_delete_no_references(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """U02 阶段 promotion/order 表不存在，引用永远 0，应允许软删."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            sku = await product_factory.sku(style)
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)
            await svc.soft_delete_sku(sku.id, user)
            await session.refresh(sku)
            assert sku.is_deleted is True
        finally:
            tenant_id_ctx.reset(token)
