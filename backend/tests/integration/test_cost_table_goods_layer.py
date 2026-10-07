"""成本表（``GET /api/skus/`` → ``SkuService.list_cost_table``）的商品层与字段权限。

本文件先放 N13（8a-7，设计 §10.3）：成本表列表的成本价 / 采购价按查看者字段权限置空，
与单条 SKU 接口同口径。后续 8a-4 / 8a-5 的成本表用例也加在这里。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.product.service import SkuService


async def _cost_row(
    session: AsyncSession, factory: Any, tenant: Any, role_code: str, style_code: str
) -> Any:
    role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
    user = await factory.user(tenant, roles=[role])
    page = await SkuService(session).list_cost_table(
        keyword=style_code,
        brand_id=None,
        include_inactive=False,
        page=1,
        page_size=20,
        user=user,
    )
    assert len(page.items) == 1
    return page.items[0]


@pytest.mark.integration
@pytest.mark.asyncio
class TestCostTableFieldPermission:
    """N13：设计师、设计助理看不到成本价 / 采购价；运营、跟单、管理员看得到。"""

    @pytest.fixture
    async def seeded(self, tenant_a: Any, product_factory: Any) -> str:
        style = await product_factory.style(style_code="CT8A01")
        await product_factory.sku(
            style,
            sku_code="CT8A01-红-M",
            cost_price=Decimal("60.00"),
            purchase_price=Decimal("50.00"),
            base_price=Decimal("199.00"),
        )
        return "CT8A01"

    @pytest.mark.parametrize("role_code", ["designer", "design_assistant"])
    async def test_hidden_for_design_roles(
        self, session: AsyncSession, tenant_a: Any, factory: Any, seeded: str, role_code: str
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            row = await _cost_row(session, factory, tenant_a, role_code, seeded)
            assert row.cost_price is None
            assert row.purchase_price is None
            # 不受字段权限保护的价格照常
            assert row.base_price == Decimal("199.00")
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize("role_code", ["operations", "merchandiser", "admin"])
    async def test_visible_for_product_roles(
        self, session: AsyncSession, tenant_a: Any, factory: Any, seeded: str, role_code: str
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            row = await _cost_row(session, factory, tenant_a, role_code, seeded)
            assert row.cost_price == Decimal("60.00")
            assert row.purchase_price == Decimal("50.00")
        finally:
            tenant_id_ctx.reset(token)
