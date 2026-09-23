"""U16 集成测试：拍单自动生成 + 刷单录入 + ROI 隔离 + 余额计算 + RLS。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment
from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import QianniuDaily
from app.modules.finance.balance_service import BalanceService
from app.modules.finance.exceptions import (
    BalanceMismatchError,
    BalanceTypeFieldMismatchError,
)
from app.modules.finance.order_adjustment_models import (
    OrderAdjustment,
)
from app.modules.finance.order_adjustment_schemas import (
    BalanceRecordCreate,
    BrushingCreate,
    OrderAdjustmentListFilters,
)
from app.modules.finance.order_adjustment_service import OrderAdjustmentService
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.production_service import ProductionService

pytestmark = pytest.mark.asyncio


class TestAutoCreateOrder:
    async def test_auto_create_and_idempotent(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promo = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
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
            cnt = (
                await session.execute(
                    select(func.count())
                    .select_from(OrderAdjustment)
                    .where(OrderAdjustment.tenant_id == tenant_a.id)
                )
            ).scalar_one()
            assert cnt == 1
        finally:
            tenant_id_ctx.reset(tok)


class TestBrushing:
    async def test_create_brushing_excludes_roi(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            result = await OrderAdjustmentService(session).create_brushing(
                BrushingCreate(
                    order_date=date(2026, 6, 2),
                    order_no="SO123",
                    style_id=style.id,
                    amount_expr="100-30",
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
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            pp = PlatformProduct(
                tenant_id=tenant_a.id,
                platform="千牛",
                platform_id=f"P{uuid4().hex[:8]}",
                style_id=style.id,
            )
            session.add(pp)
            await session.flush()
            day = date(2026, 6, 5)
            session.add(
                QianniuDaily(
                    tenant_id=tenant_a.id,
                    platform_product_id=pp.id,
                    platform_id_snapshot=pp.platform_id,
                    date=day,
                    visitors=100,
                    pay_amount=Decimal("1000.00"),
                    pay_orders=10,
                )
            )
            # 刷单 200 剔除
            session.add(
                OrderAdjustment(
                    tenant_id=tenant_a.id,
                    order_type="刷单",
                    order_date=day,
                    style_id=style.id,
                    amount=Decimal("200.00"),
                    exclude_from_roi=True,
                    status="待付款",
                )
            )
            await session.flush()
            await session.commit()

            svc = ProductionService(session)
            excl = await svc.get_report(tenant_a.id, (day, day), exclude_brushing=True)
            incl = await svc.get_report(tenant_a.id, (day, day), exclude_brushing=False)
            excl_row = next(r for r in excl.items if r.style_id == style.id)
            incl_row = next(r for r in incl.items if r.style_id == style.id)
            assert excl_row.pay_amount == Decimal("800.00")  # 1000 - 200
            assert incl_row.pay_amount == Decimal("1000.00")
        finally:
            tenant_id_ctx.reset(tok)


class TestBalance:
    async def test_balance_auto_compute(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BalanceService(session)
            r1 = await svc.add_record(
                BalanceRecordCreate(
                    record_date=date(2026, 6, 1),
                    record_type="充值",
                    income=Decimal("1000"),
                ),
                user,
            )
            assert r1.balance_after == Decimal("1000")
            r2 = await svc.add_record(
                BalanceRecordCreate(
                    record_date=date(2026, 6, 2),
                    record_type="推广支出",
                    expense=Decimal("300"),
                ),
                user,
            )
            assert r2.balance_after == Decimal("700")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_balance_mismatch_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BalanceService(session)
            with pytest.raises(BalanceMismatchError):
                await svc.add_record(
                    BalanceRecordCreate(
                        record_date=date(2026, 6, 1),
                        record_type="充值",
                        income=Decimal("1000"),
                        expected_balance=Decimal("999"),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(tok)

    async def test_balance_type_field_mismatch(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BalanceService(session)
            with pytest.raises(BalanceTypeFieldMismatchError):
                await svc.add_record(
                    BalanceRecordCreate(
                        record_date=date(2026, 6, 1),
                        record_type="充值",
                        expense=Decimal("100"),  # 充值不应填 expense
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(tok)


class TestRls:
    async def test_balance_tenant_isolation(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        factory: Any,
        finance_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_b.id)
        try:
            user_b = await factory.user(tenant_b, roles=[finance_role])
            await BalanceService(session).add_record(
                BalanceRecordCreate(
                    record_date=date(2026, 6, 1),
                    record_type="充值",
                    income=Decimal("5000"),
                ),
                user_b,
            )
            await session.commit()
            # tenant_a last_balance 不含 tenant_b 数据
            from app.modules.finance.order_adjustment_repository import (
                BalanceRecordRepository,
            )

            prev_a = await BalanceRecordRepository(session).last_balance(tenant_a.id)
            assert prev_a == Decimal("0")
        finally:
            tenant_id_ctx.reset(tok)


class TestOrderAdjustmentListFilters:
    """拍单 / 刷单合并页的服务端筛选与分页。

    两类单据本来就在同一张表，合并页面后条数翻倍，原先无分页的 limit 写法
    会让超出上限的单据直接消失，所以筛选与分页都必须落在服务端。
    """

    async def _seed(self, session: AsyncSession, tenant: Any, style: Any) -> None:
        rows = [
            # order_type, order_date, order_no, blogger, amount, status, exclude_roi
            ("拍单", date(2026, 9, 1), "TAO-001", "wx_alice", "100.00", "待付款", False),
            ("拍单", date(2026, 9, 5), "TAO-002", "wx_bob", "800.00", "已付款", False),
            ("刷单", date(2026, 9, 3), "BRUSH-001", "wx_alice", "300.00", "待付款", True),
            ("刷单", None, None, None, "50.00", "待付款", True),
        ]
        for order_type, day, order_no, blogger, amount, status, exclude_roi in rows:
            session.add(
                OrderAdjustment(
                    tenant_id=tenant.id,
                    order_type=order_type,
                    order_date=day,
                    order_no=order_no,
                    blogger_identifier=blogger,
                    style_id=style.id,
                    amount=Decimal(amount),
                    status=status,
                    exclude_from_roi=exclude_roi,
                )
            )
        await session.flush()

    async def test_no_filter_returns_both_types(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)
            page = await OrderAdjustmentService(session).list()
            assert page.total == 4
            assert {i.order_type for i in page.items} == {"拍单", "刷单"}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_filter_by_order_type(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)
            svc = OrderAdjustmentService(session)
            page = await svc.list(filters=OrderAdjustmentListFilters(order_type="刷单"))
            assert page.total == 2
            assert all(i.order_type == "刷单" for i in page.items)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_filter_by_status_and_amount_range(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)
            svc = OrderAdjustmentService(session)

            paid = await svc.list(filters=OrderAdjustmentListFilters(status="已付款"))
            assert {i.order_no for i in paid.items} == {"TAO-002"}

            mid = await svc.list(
                filters=OrderAdjustmentListFilters(
                    amount_min=Decimal("100.00"), amount_max=Decimal("300.00")
                )
            )
            assert {i.order_no for i in mid.items} == {"TAO-001", "BRUSH-001"}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_keyword_matches_order_no_or_blogger(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)
            svc = OrderAdjustmentService(session)

            by_no = await svc.list(filters=OrderAdjustmentListFilters(keyword="BRUSH"))
            assert {i.order_no for i in by_no.items} == {"BRUSH-001"}

            by_blogger = await svc.list(filters=OrderAdjustmentListFilters(keyword="alice"))
            assert {i.order_no for i in by_blogger.items} == {"TAO-001", "BRUSH-001"}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_date_range_excludes_null_order_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """日期区间筛选时 order_date 为空的行不该被算进来。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)
            page = await OrderAdjustmentService(session).list(
                filters=OrderAdjustmentListFilters(
                    order_date_from=date(2026, 9, 1), order_date_to=date(2026, 9, 3)
                )
            )
            assert {i.order_no for i in page.items} == {"TAO-001", "BRUSH-001"}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_null_order_date_sorted_last(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)
            page = await OrderAdjustmentService(session).list()
            assert page.items[-1].order_date is None
            assert page.items[0].order_no == "TAO-002"  # 日期最新
        finally:
            tenant_id_ctx.reset(tok)

    async def test_exclude_from_roi_filter(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)
            svc = OrderAdjustmentService(session)
            excluded = await svc.list(filters=OrderAdjustmentListFilters(exclude_from_roi=True))
            assert excluded.total == 2
            assert all(i.exclude_from_roi for i in excluded.items)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_pagination_total_is_filtered_count(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)
            page = await OrderAdjustmentService(session).list(page=1, page_size=2)
            assert len(page.items) == 2
            assert page.total == 4
            assert page.page_size == 2
        finally:
            tenant_id_ctx.reset(tok)

    async def test_style_snapshot_enriched(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """款式编码/名称是反范式富化出来的，不是表里存的。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="OA001", style_name="测试连衣裙")
            await self._seed(session, tenant_a, style)
            page = await OrderAdjustmentService(session).list()
            assert all(i.style_code == "OA001" for i in page.items)
            assert all(i.style_name == "测试连衣裙" for i in page.items)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_has_payment_qr_filter(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            await self._seed(session, tenant_a, style)

            attachment = Attachment(
                tenant_id=tenant_a.id,
                bucket="private",
                r2_key=f"tenants/{tenant_a.id}/order-qr/{uuid4().hex}.png",
                purpose="order_adjustment_payment_qr",
                filename="qr.png",
                mime_type="image/png",
                size_bytes=1024,
                status="ready",
                created_by=user.id,
            )
            session.add(attachment)
            await session.flush()

            target = (
                await session.execute(
                    select(OrderAdjustment).where(OrderAdjustment.order_no == "TAO-001")
                )
            ).scalar_one()
            target.payment_qr_attachment_id = attachment.id
            await session.flush()

            svc = OrderAdjustmentService(session)
            with_qr = await svc.list(filters=OrderAdjustmentListFilters(has_payment_qr=True))
            assert {i.order_no for i in with_qr.items} == {"TAO-001"}

            without_qr = await svc.list(filters=OrderAdjustmentListFilters(has_payment_qr=False))
            assert with_qr.total + without_qr.total == 4
        finally:
            tenant_id_ctx.reset(tok)
