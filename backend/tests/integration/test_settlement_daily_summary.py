"""U05 集成测试：双口径当日汇总（FB7 + FB8）。

- 口径 B (as_of)：截至当日各状态快照 + outstanding_total
- 口径 A (activity)：当天发生的动作（含 audit_log JOIN）
- 权限：非 PAYMENT_VISIBLE 角色 → FieldPermissionDenied
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.finance.exceptions import FieldPermissionDenied
from app.modules.finance.service import SettlementService
from app.modules.promotion.urge_calculator import get_today


@pytest.mark.integration
@pytest.mark.asyncio
class TestDailySummaryAsOf:
    async def test_as_of_buckets_and_outstanding(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """FB7 口径 B：各状态计数 + outstanding = 待核查+待付款+待财务付款。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            # 3 待核查 + 2 待付款 + 1 已付款
            for _ in range(3):
                await settlement_factory.settlement(
                    style=style, blogger=blogger,
                    settlement_status="待核查", total_amount=Decimal("100.00"),
                )
            for _ in range(2):
                await settlement_factory.settlement(
                    style=style, blogger=blogger,
                    settlement_status="待付款", total_amount=Decimal("200.00"),
                )
            await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="已付款", total_amount=Decimal("500.00"),
            )
            await session.flush()

            svc = SettlementService(session)
            resp = await svc.get_daily_summary_as_of(
                date_value=get_today(), user=user
            )
            assert resp.kind == "as_of"
            assert resp.as_of.pending_review.count == 3
            assert resp.as_of.pending_payment.count == 2
            assert resp.as_of.paid.count == 1
            # outstanding = 3 + 2 + 0 (待财务付款) = 5
            assert resp.outstanding_total.count == 5
            assert resp.outstanding_total.total_amount == Decimal("700.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_as_of_denied_for_pr_role(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            svc = SettlementService(session)
            with pytest.raises(FieldPermissionDenied):
                await svc.get_daily_summary_as_of(
                    date_value=get_today(), user=user
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestDailySummaryActivity:
    async def test_activity_newly_created(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """FB7 口径 A：当天新建计数。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            for _ in range(4):
                await settlement_factory.settlement(
                    style=style, blogger=blogger,
                    settlement_status="待核查", total_amount=Decimal("150.00"),
                )
            await session.flush()

            svc = SettlementService(session)
            resp = await svc.get_daily_summary_activity(
                date_value=get_today(), user=user
            )
            assert resp.kind == "activity"
            assert resp.activity.newly_created.count == 4
            assert resp.activity.newly_created.total_amount == Decimal("600.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_activity_uses_today_when_no_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """FB8：date_value=None → 默认 get_today()（时区入口）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待核查", total_amount=Decimal("100.00"),
            )
            await session.flush()

            svc = SettlementService(session)
            resp = await svc.get_daily_summary_activity(
                date_value=None, user=user
            )
            assert resp.date == get_today()
            assert resp.activity.newly_created.count >= 1
        finally:
            tenant_id_ctx.reset(token)
