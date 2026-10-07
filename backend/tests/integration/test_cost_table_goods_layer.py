"""成本表（``GET /api/skus/`` → ``SkuService.list_cost_table``）的商品层与字段权限。

本文件先放 N13（8a-7，设计 §10.3）：成本表列表的成本价 / 采购价按查看者字段权限置空，
与单条 SKU 接口同口径。后续 8a-4 / 8a-5 的成本表用例也加在这里。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
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


async def _goods(
    session: AsyncSession, tenant: Any, code: str, styles: list[Any], **kw: Any
) -> Any:
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=code,
        goods_title=f"{code} 全称",
        is_suit=len(styles) >= 2,
        **kw,
    )
    session.add(goods)
    await session.flush()
    for order, style in enumerate(styles):
        session.add(
            GoodsStyleItem(
                tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id, sort_order=order
            )
        )
    await session.flush()
    return goods


@pytest.mark.integration
@pytest.mark.asyncio
class TestCostTableGoodsLayer:
    """AC 30（8a-4，FR-4.7）：简称 / 品牌显示款式的主商品（非套装优先），按商品层品牌筛选。"""

    async def _page(self, session: AsyncSession, factory: Any, tenant: Any, **kw: Any) -> Any:
        role = (await session.execute(select(Role).where(Role.code == "admin"))).scalar_one()
        user = await factory.user(tenant, roles=[role])
        params: dict[str, Any] = {"keyword": None, "brand_id": None, **kw}
        return await SkuService(session).list_cost_table(
            include_inactive=False, page=1, page_size=50, user=user, **params
        )

    async def test_main_goods_short_name_and_brand(
        self, session: AsyncSession, tenant_a: Any, factory: Any, product_factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            tag = uuid4().hex[:6]
            single_brand = await product_factory.brand(brand_name=f"单品牌{tag}")
            suit_brand = await product_factory.brand(brand_name=f"套装牌{tag}")
            style_brand = await product_factory.brand(brand_name=f"款式牌{tag}")
            style = await product_factory.style(
                style_code=f"CT4{tag}", short_name="款式层简称", brand_id=style_brand.id
            )
            mate = await product_factory.style(style_code=f"CT4{tag}B")
            await product_factory.sku(style, sku_code=f"CT4{tag}-1")
            # 套装编码排在前面也不影响：非套装优先
            await _goods(
                session,
                tenant_a,
                f"A-SUIT-{tag}",
                [style, mate],
                short_name="套装简称",
                brand_id=suit_brand.id,
            )
            await _goods(
                session,
                tenant_a,
                f"CT4{tag}",
                [style],
                short_name=f"单品简{tag}",
                brand_id=single_brand.id,
            )

            page = await self._page(session, factory, tenant_a, keyword=f"CT4{tag}-1")
            [row] = page.items
            assert (row.short_name, row.brand_name) == (f"单品简{tag}", f"单品牌{tag}")

            # 关键词另搜主商品简称
            page = await self._page(session, factory, tenant_a, keyword=f"单品简{tag}")
            assert [r.sku_code for r in page.items] == [f"CT4{tag}-1"]

            # 品牌筛选按主商品的品牌：单品牌命中，套装与款式层的品牌都不命中
            page = await self._page(session, factory, tenant_a, brand_id=single_brand.id)
            assert [r.sku_code for r in page.items] == [f"CT4{tag}-1"]
            for other in (suit_brand.id, style_brand.id):
                page = await self._page(session, factory, tenant_a, brand_id=other)
                assert page.items == []
        finally:
            tenant_id_ctx.reset(token)

    async def test_style_without_goods_shows_empty(
        self, session: AsyncSession, tenant_a: Any, factory: Any, product_factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            tag = uuid4().hex[:6]
            style = await product_factory.style(style_code=f"CT5{tag}", short_name="款式层简称")
            await product_factory.sku(style, sku_code=f"CT5{tag}-1")
            page = await self._page(session, factory, tenant_a, keyword=f"CT5{tag}-1")
            [row] = page.items
            assert (row.short_name, row.brand_name) == (None, None)
        finally:
            tenant_id_ctx.reset(token)
