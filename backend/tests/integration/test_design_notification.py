"""U10a 集成测试：状态推进通知写入 + 自动核价写 SKU。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.design.schemas import (
    CostBreakdown,
    CostingSubmit,
    CraftSubmit,
    DesignCreate,
    FabricSubmit,
    GradingSubmit,
    PatternSubmit,
)
from app.modules.design.service import DesignService
from app.modules.product.models import Sku
from app.modules.wecom.models import Notification


async def _role(session: AsyncSession, code: str, name: str) -> Role:
    r = (await session.execute(select(Role).where(Role.code == code))).scalar_one_or_none()
    if r is None:
        r = Role(id=uuid4(), code=code, name=name, is_system=True)
        session.add(r)
        await session.flush()
    return r


@pytest.mark.integration
@pytest.mark.asyncio
class TestDesignNotification:
    async def test_submit_fabric_notifies_pattern_maker(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            designer = await factory.user(tenant_a, roles=[await _role(session, "designer", "设计师")])
            pm = await factory.user(tenant_a, roles=[await _role(session, "pattern_maker", "版师")])
            svc = DesignService(session)
            d = await svc.create_design(DesignCreate(style_code="NT1", style_name="款"), designer)
            await svc.submit_fabric(d.id, FabricSubmit(fabrics=[{"name": "棉"}]), designer)

            cnt = (
                await session.execute(
                    select(func.count()).select_from(Notification).where(
                        Notification.user_id == pm.id
                    )
                )
            ).scalar_one()
            assert cnt >= 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_no_pattern_maker_skips_notify(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        """该角色租户内无人时不报错（BR-U10a-62）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            designer = await factory.user(tenant_a, roles=[await _role(session, "designer", "设计师")])
            svc = DesignService(session)
            d = await svc.create_design(DesignCreate(style_code="NT2", style_name="款"), designer)
            # 无 pattern_maker 用户 → 不报错
            d = await svc.submit_fabric(d.id, FabricSubmit(fabrics=[{"name": "棉"}]), designer)
            assert d.design_status == "制版中"
        finally:
            tenant_id_ctx.reset(token)

    async def test_auto_costing_writes_sku_cost_price(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            designer = await factory.user(tenant_a, roles=[await _role(session, "designer", "设计师")])
            pm = await factory.user(tenant_a, roles=[await _role(session, "pattern_maker", "版师")])
            mer = await factory.user(tenant_a, roles=[await _role(session, "merchandiser", "跟单")])
            da = await factory.user(tenant_a, roles=[await _role(session, "design_assistant", "设计助理")])
            svc = DesignService(session)

            d = await svc.create_design(DesignCreate(style_code="NT3", style_name="款"), designer)
            sid = d.id
            # 给 style 添加一个 active SKU
            sku = Sku(
                tenant_id=tenant_a.id, style_id=sid, sku_code="NT3-R-M",
                color="红", size="M", sourcing_type="自产", is_active=True, is_deleted=False,
            )
            session.add(sku)
            await session.flush()

            # 走到待补全
            await svc.submit_fabric(sid, FabricSubmit(fabrics=[{"name": "棉"}]), designer)
            await svc.submit_pattern(sid, PatternSubmit(pattern_no="V1"), pm)
            await svc.submit_grading(sid, GradingSubmit(grading_data={"S": 1}), pm)
            await svc.submit_craft(sid, CraftSubmit(craft_info={"x": 1}), mer)
            # 核价：10+5+5 = 20
            await svc.submit_costing(
                sid,
                CostingSubmit(cost_breakdown=CostBreakdown(
                    fabric_cost=Decimal("10"), accessory_cost=Decimal("5"), craft_cost=Decimal("5"))),
                da,
            )
            await session.refresh(sku)
            assert sku.cost_price == Decimal("20.00")
        finally:
            tenant_id_ctx.reset(token)
