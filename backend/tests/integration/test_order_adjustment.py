"""U16 集成测试：拍单自动生成 + 刷单录入 + ROI 隔离 + 余额计算 + RLS。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import QianniuDaily
from app.modules.finance.balance_service import BalanceService
from app.modules.finance.exceptions import (
    BalanceMismatchError,
    BalanceTypeFieldMismatchError,
)
from app.modules.finance.order_adjustment_models import (
    BalanceRecord,
    OrderAdjustment,
)
from app.modules.finance.order_adjustment_schemas import (
    BalanceRecordCreate,
    BrushingCreate,
)
from app.modules.finance.order_adjustment_service import OrderAdjustmentService
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.production_service import ProductionService

pytestmark = pytest.mark.asyncio


class TestAutoCreateOrder:
    async def test_auto_create_and_idempotent(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any,
        product_factory: Any, blogger_factory: Any, promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promo = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=pr,
                cooperation_date=date(2026, 6, 1),
            )
            promo.in_store_order = True
            await session.flush()

            svc = OrderAdjustmentService(session)
            row1 = await svc.auto_create_from_promotion(promo)
            assert row1 is not None
            assert row1.order_type == "拍单"
            assert row1.promotion_id == promo.id
            # 幂等：二次调用返回已存在，不新建
            row2 = await svc.auto_create_from_promotion(promo)
            assert row2.id == row1.id
            cnt = (await session.execute(
                select(func.count()).select_from(OrderAdjustment).where(
                    OrderAdjustment.tenant_id == tenant_a.id
                )
            )).scalar_one()
            assert cnt == 1
        finally:
            tenant_id_ctx.reset(tok)


class TestBrushing:
    async def test_create_brushing_excludes_roi(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        finance_role: Any, product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            result = await OrderAdjustmentService(session).create_brushing(
                BrushingCreate(
                    order_date=date(2026, 6, 2), order_no="SO123",
                    style_id=style.id, amount_expr="100-30",
                ),
                user,
            )
            assert result["amount"] == Decimal("70")
            assert result["exclude_from_roi"] is True
            assert result["duplicate"] is False
        finally:
            tenant_id_ctx.reset(tok)


class TestRoiIsolation:
    async def test_brushing_excluded_from_production(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            pp = PlatformProduct(
                tenant_id=tenant_a.id, platform="千牛",
                platform_id=f"P{uuid4().hex[:8]}", style_id=style.id,
            )
            session.add(pp)
            await session.flush()
            day = date(2026, 6, 5)
            session.add(QianniuDaily(
                tenant_id=tenant_a.id, platform_product_id=pp.id,
                platform_id_snapshot=pp.platform_id, date=day,
                visitors=100, pay_amount=Decimal("1000.00"), pay_orders=10,
            ))
            # 刷单 200 剔除
            session.add(OrderAdjustment(
                tenant_id=tenant_a.id, order_type="刷单", order_date=day,
                style_id=style.id, amount=Decimal("200.00"),
                exclude_from_roi=True, status="待付款",
            ))
            await session.flush()
            await session.commit()

            svc = ProductionService(session)
            excl = await svc.get_report(tenant_a.id, (day, day), exclude_brushing=True)
            incl = await svc.get_report(tenant_a.id, (day, day), exclude_brushing=False)
            excl_row = [r for r in excl.items if r.style_id == style.id][0]
            incl_row = [r for r in incl.items if r.style_id == style.id][0]
            assert excl_row.pay_amount == Decimal("800.00")   # 1000 - 200
            assert incl_row.pay_amount == Decimal("1000.00")
        finally:
            tenant_id_ctx.reset(tok)


class TestBalance:
    async def test_balance_auto_compute(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        finance_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BalanceService(session)
            r1 = await svc.add_record(BalanceRecordCreate(
                record_date=date(2026, 6, 1), record_type="充值",
                income=Decimal("1000"),
            ), user)
            assert r1.balance_after == Decimal("1000")
            r2 = await svc.add_record(BalanceRecordCreate(
                record_date=date(2026, 6, 2), record_type="推广支出",
                expense=Decimal("300"),
            ), user)
            assert r2.balance_after == Decimal("700")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_balance_mismatch_rejected(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        finance_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BalanceService(session)
            with pytest.raises(BalanceMismatchError):
                await svc.add_record(BalanceRecordCreate(
                    record_date=date(2026, 6, 1), record_type="充值",
                    income=Decimal("1000"), expected_balance=Decimal("999"),
                ), user)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_balance_type_field_mismatch(
        self, session: AsyncSession, tenant_a: Any, factory: Any,
        finance_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BalanceService(session)
            with pytest.raises(BalanceTypeFieldMismatchError):
                await svc.add_record(BalanceRecordCreate(
                    record_date=date(2026, 6, 1), record_type="充值",
                    expense=Decimal("100"),  # 充值不应填 expense
                ), user)
        finally:
            tenant_id_ctx.reset(tok)


class TestRls:
    async def test_balance_tenant_isolation(
        self, session: AsyncSession, tenant_a: Any, tenant_b: Any, factory: Any,
        finance_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_b.id)
        try:
            user_b = await factory.user(tenant_b, roles=[finance_role])
            await BalanceService(session).add_record(BalanceRecordCreate(
                record_date=date(2026, 6, 1), record_type="充值",
                income=Decimal("5000"),
            ), user_b)
            await session.commit()
            # tenant_a last_balance 不含 tenant_b 数据
            from app.modules.finance.order_adjustment_repository import (
                BalanceRecordRepository,
            )
            prev_a = await BalanceRecordRepository(session).last_balance(tenant_a.id)
            assert prev_a == Decimal("0")
        finally:
            tenant_id_ctx.reset(tok)
