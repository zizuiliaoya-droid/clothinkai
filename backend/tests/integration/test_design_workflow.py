"""U10a 集成测试：设计制版状态机端到端 J1 + reject + cancel。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IllegalStateTransitionError, PermissionDeniedError
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


async def _role(session: AsyncSession, code: str, name: str) -> Role:
    r = (await session.execute(select(Role).where(Role.code == code))).scalar_one_or_none()
    if r is None:
        r = Role(id=uuid4(), code=code, name=name, is_system=True)
        session.add(r)
        await session.flush()
    return r


async def _users(session: AsyncSession, factory: Any, tenant: Any) -> dict[str, Any]:
    return {
        "designer": await factory.user(tenant, roles=[await _role(session, "designer", "设计师")]),
        "pattern_maker": await factory.user(tenant, roles=[await _role(session, "pattern_maker", "版师")]),
        "merchandiser": await factory.user(tenant, roles=[await _role(session, "merchandiser", "跟单")]),
        "design_assistant": await factory.user(tenant, roles=[await _role(session, "design_assistant", "设计助理")]),
        "admin": await factory.user(tenant, roles=[await _role(session, "admin", "管理员")]),
    }


@pytest.mark.integration
@pytest.mark.asyncio
class TestDesignWorkflow:
    async def test_full_journey_to_mass_production(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            u = await _users(session, factory, tenant_a)
            svc = DesignService(session)

            d = await svc.create_design(
                DesignCreate(style_code="DSG1", style_name="测试连衣裙"), u["designer"]
            )
            assert d.design_status == "设计中"
            sid = d.id

            d = await svc.submit_fabric(sid, FabricSubmit(fabrics=[{"name": "棉"}]), u["designer"])
            assert d.design_status == "制版中"

            await svc.submit_pattern(sid, PatternSubmit(pattern_no="V001"), u["pattern_maker"])
            d = await svc.submit_grading(sid, GradingSubmit(grading_data={"S": 1}), u["pattern_maker"])
            assert d.design_status == "工艺录入"

            d = await svc.submit_craft(sid, CraftSubmit(craft_info={"sew": "x"}), u["merchandiser"])
            assert d.design_status == "待补全"

            d = await svc.submit_costing(
                sid,
                CostingSubmit(cost_breakdown=CostBreakdown(
                    fabric_cost=Decimal("10"), accessory_cost=Decimal("5"), craft_cost=Decimal("5"))),
                u["design_assistant"],
            )
            assert d.design_status == "待核价"

            d = await svc.confirm_price(sid, u["merchandiser"])
            assert d.design_status == "大货"
            assert d.available_actions == []  # 终态无动作（merchandiser）
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_back_to_previous(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            u = await _users(session, factory, tenant_a)
            svc = DesignService(session)
            d = await svc.create_design(DesignCreate(style_code="DSG2", style_name="款2"), u["designer"])
            await svc.submit_fabric(d.id, FabricSubmit(fabrics=[{"name": "棉"}]), u["designer"])
            # 制版中 reject → 设计中
            d = await svc.reject(d.id, "设计稿不全", u["pattern_maker"])
            assert d.design_status == "设计中"
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_requires_reason(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        from app.modules.design.exceptions import RejectReasonRequiredError

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            u = await _users(session, factory, tenant_a)
            svc = DesignService(session)
            d = await svc.create_design(DesignCreate(style_code="DSG3", style_name="款3"), u["designer"])
            await svc.submit_fabric(d.id, FabricSubmit(fabrics=[{"name": "棉"}]), u["designer"])
            with pytest.raises(RejectReasonRequiredError):
                await svc.reject(d.id, "  ", u["pattern_maker"])
        finally:
            tenant_id_ctx.reset(token)

    async def test_cancel_irreversible(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            u = await _users(session, factory, tenant_a)
            svc = DesignService(session)
            d = await svc.create_design(DesignCreate(style_code="DSG4", style_name="款4"), u["designer"])
            d = await svc.cancel(d.id, "不再开发", u["admin"])
            assert d.design_status == "已取消"
            # 已取消后推进 → 422
            with pytest.raises(IllegalStateTransitionError):
                await svc.submit_fabric(d.id, FabricSubmit(fabrics=[{"name": "棉"}]), u["designer"])
        finally:
            tenant_id_ctx.reset(token)

    async def test_cancel_requires_admin(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            u = await _users(session, factory, tenant_a)
            svc = DesignService(session)
            d = await svc.create_design(DesignCreate(style_code="DSG5", style_name="款5"), u["designer"])
            with pytest.raises(PermissionDeniedError):
                await svc.cancel(d.id, "x", u["designer"])
        finally:
            tenant_id_ctx.reset(token)

    async def test_illegal_transition(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            u = await _users(session, factory, tenant_a)
            svc = DesignService(session)
            d = await svc.create_design(DesignCreate(style_code="DSG6", style_name="款6"), u["designer"])
            # 设计中直接 confirm_price → 非法
            with pytest.raises(IllegalStateTransitionError):
                await svc.confirm_price(d.id, u["merchandiser"])
        finally:
            tenant_id_ctx.reset(token)
