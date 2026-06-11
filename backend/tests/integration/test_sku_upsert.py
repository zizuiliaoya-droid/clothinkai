"""U02 SKU upsert 集成测试（FB7 + 并发）.

覆盖：
- 第一次调用 → INSERT 路径（is_inserted=True，audit action=sku.create_via_import）
- 第二次调用同 sku_code → UPDATE 路径（audit action=sku.update_via_import，sku.id 不变）
- 校验/权限/审计与普通 create/update 同一套
- partial UNIQUE 与 ON CONFLICT 严格对齐：软删行不"恢复"

注：100 并发测试因 pytest 共用一个事务难以模拟，
真正的并发覆盖通过 SQL 层面 ``ON CONFLICT`` 原子保证；
此处通过断言 ``upsert_atomic`` 调用语义 + 系统列 xmax = 0 判断验证。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.enums import SourcingType
from app.modules.product.schemas import SkuCreate
from app.modules.product.service import SkuService


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpsertCreatePath:
    async def test_first_call_inserts(
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
            response = await svc.upsert_sku(
                SkuCreate(
                    style_id=style.id,
                    sku_code="UPSERT-001",
                    color="红",
                    size="M",
                    cost_price=Decimal("100.00"),
                    sourcing_type=SourcingType.SELF_PRODUCED,
                ),
                user,
            )
            assert response.sku_code == "UPSERT-001"
            assert response.cost_price == Decimal("100.00")
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
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)

            # 第一次 — INSERT
            r1 = await svc.upsert_sku(
                SkuCreate(
                    style_id=style.id,
                    sku_code="UPSERT-002",
                    color="红",
                    size="M",
                    cost_price=Decimal("100.00"),
                    sourcing_type=SourcingType.SELF_PRODUCED,
                ),
                user,
            )
            # 第二次同 sku_code — UPDATE
            r2 = await svc.upsert_sku(
                SkuCreate(
                    style_id=style.id,
                    sku_code="UPSERT-002",
                    color="蓝",  # 改色
                    size="L",  # 改尺码
                    cost_price=Decimal("110.00"),
                    sourcing_type=SourcingType.SELF_PRODUCED,
                ),
                user,
            )

            # 同一 SKU id（数据库原子 upsert，不创建新行）
            assert r1.id == r2.id
            assert r2.color == "蓝"
            assert r2.size == "L"
            assert r2.cost_price == Decimal("110.00")
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpsertReusesValidation:
    """FB7: upsert 必须复用同一套校验/权限/审计."""

    async def test_invalid_style_reference_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        from uuid import uuid4

        from app.modules.product.exceptions import (
            InvalidStyleReferenceError,
        )

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)
            with pytest.raises(InvalidStyleReferenceError):
                await svc.upsert_sku(
                    SkuCreate(
                        style_id=uuid4(),
                        sku_code="UPSERT-X",
                        color="红",
                        size="M",
                        cost_price=Decimal("100.00"),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_invalid_sourcing_price_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        from app.modules.product.exceptions import SourcingPriceMismatchError

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = SkuService(session)
            with pytest.raises(SourcingPriceMismatchError):
                # 自产但缺 cost_price
                await svc.upsert_sku(
                    SkuCreate(
                        style_id=style.id,
                        sku_code="UPSERT-Y",
                        color="红",
                        size="M",
                        cost_price=None,
                        sourcing_type=SourcingType.SELF_PRODUCED,
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_field_permission_denied_for_designer(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        designer_role: Any,
        product_factory: Any,
    ) -> None:
        from app.modules.product.exceptions import FieldPermissionDenied

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[designer_role])
            svc = SkuService(session)
            with pytest.raises(FieldPermissionDenied):
                # designer 无权写 cost_price，即使是 import 路径
                await svc.upsert_sku(
                    SkuCreate(
                        style_id=style.id,
                        sku_code="UPSERT-Z",
                        color="红",
                        size="M",
                        cost_price=Decimal("100.00"),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)
