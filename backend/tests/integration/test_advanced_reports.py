"""U14 报表进阶集成测试。

覆盖：工作进度月度聚合 / 爆款约篇 set+list 达标 / 店铺聚合+手动 upsert /
投产跨表+周环比 / RLS 隔离。

测试引擎用 bypass 角色（RLS OFF）→ 聚合查询已显式 WHERE tenant_id。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import AdDaily, QianniuDaily
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.advanced_schemas import (
    StoreDailyManualUpdate,
    TargetCreate,
)
from app.modules.report.production_service import ProductionService
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.target_planning_service import TargetPlanningService
from app.modules.report.work_progress_service import WorkProgressService

pytestmark = pytest.mark.asyncio


async def _platform_product(
    session: AsyncSession, tenant: Any, style: Any, platform: str = "千牛"
) -> PlatformProduct:
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform=platform,
        platform_id=f"P{uuid4().hex[:8]}",
        style_id=style.id,
    )
    session.add(pp)
    await session.flush()
    return pp


async def _qianniu(
    session: AsyncSession,
    tenant: Any,
    pp: PlatformProduct,
    day: date,
    *,
    pay: str = "1000.00",
    visitors: int = 100,
    orders: int = 10,
    extra: dict | None = None,
) -> None:
    session.add(
        QianniuDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            visitors=visitors,
            pay_amount=Decimal(pay),
            pay_orders=orders,
            extra=extra,
        )
    )
    await session.flush()


async def _ad(
    session: AsyncSession,
    tenant: Any,
    pp: PlatformProduct,
    day: date,
    *,
    cost: str = "200.00",
) -> None:
    session.add(
        AdDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            cost=Decimal(cost),
        )
    )
    await session.flush()


class TestWorkProgress:
    async def test_month_aggregation_by_pr(
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
            # 同 PR 本月 2 篇：1 已发布(含 like) + 1 未发布
            await promotion_factory.promotion(
                style=style, blogger=blogger, pr=pr,
                cooperation_date=date(2026, 5, 10),
                publish_status="已发布", like_count=600,
                cost_snapshot=Decimal("300"),
            )
            await promotion_factory.promotion(
                style=style, blogger=blogger, pr=pr,
                cooperation_date=date(2026, 5, 20),
                publish_status="未发布",
            )
            # 上月 1 篇（不应计入 2026-05）
            await promotion_factory.promotion(
                style=style, blogger=blogger, pr=pr,
                cooperation_date=date(2026, 4, 15),
                publish_status="已发布", like_count=10,
            )
            await session.commit()

            rows = await WorkProgressService(session).get_for_month(
                tenant_a.id, "2026-05"
            )
            mine = [r for r in rows if r.pr_id == pr.id]
            assert len(mine) == 1
            row = mine[0]
            assert row.quote_count == 2
            assert row.publish_count == 1
            assert row.hit_count == 1  # like 600 >= HIT_STAT_THRESHOLD(500)
            assert row.month_complete_rate == Decimal("0.5000")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_invalid_month_raises(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        from app.modules.report.exceptions import ReportInvalidTimeRangeError

        with pytest.raises(ReportInvalidTimeRangeError):
            await WorkProgressService(session).get_for_month(
                tenant_a.id, "2026-13"
            )


class TestTargetPlanning:
    async def test_set_and_list_with_status(
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
            svc = TargetPlanningService(session)
            # 设目标 3，实际 2 篇 → 未达标 gap=-1
            await svc.set_target(
                TargetCreate(
                    pr_id=pr.id, style_id=style.id,
                    period_month="2026-05", min_target=3,
                ),
                pr,
            )
            for _ in range(2):
                await promotion_factory.promotion(
                    style=style, blogger=blogger, pr=pr,
                    cooperation_date=date(2026, 5, 8),
                )
            await session.commit()

            rows = await svc.list_with_actuals(tenant_a.id, "2026-05")
            assert len(rows) == 1
            assert rows[0].min_target == 3
            assert rows[0].actual_count == 2
            assert rows[0].status == "未达标"
            assert rows[0].gap == -1
        finally:
            tenant_id_ctx.reset(tok)

    async def test_upsert_overwrites_target(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            svc = TargetPlanningService(session)
            base = TargetCreate(
                pr_id=pr.id, style_id=style.id,
                period_month="2026-05", min_target=3,
            )
            await svc.set_target(base, pr)
            await svc.set_target(base.model_copy(update={"min_target": 8}), pr)
            await session.commit()
            rows = await svc.list_with_actuals(tenant_a.id, "2026-05")
            assert len(rows) == 1
            assert rows[0].min_target == 8
        finally:
            tenant_id_ctx.reset(tok)


class TestStoreDaily:
    async def test_aggregate_with_manual_join(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        operations_role: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            ops = await factory.user(tenant_a, roles=[operations_role])
            style = await product_factory.style()
            pp = await _platform_product(session, tenant_a, style)
            day = date(2026, 5, 15)
            # 同日两商品 → SUM 聚合
            await _qianniu(session, tenant_a, pp, day, pay="600.00", visitors=60, orders=6)
            pp2 = await _platform_product(session, tenant_a, style)
            await _qianniu(session, tenant_a, pp2, day, pay="400.00", visitors=40, orders=4)
            svc = StoreDailyService(session)
            # 手动 upsert 广告花费
            await svc.upsert_manual(
                tenant_a.id, day,
                StoreDailyManualUpdate(ad_spend_total=Decimal("150.00")),
                ops,
            )
            await session.commit()

            rows = await svc.get_dashboard(tenant_a.id, (day, day))
            assert len(rows) == 1
            assert rows[0].visitors == 100
            assert rows[0].pay_amount == Decimal("1000.00")
            assert rows[0].pay_orders == 10
            assert rows[0].ad_spend_total == Decimal("150.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_manual_upsert_overwrites(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        operations_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            ops = await factory.user(tenant_a, roles=[operations_role])
            day = date(2026, 5, 16)
            svc = StoreDailyService(session)
            await svc.upsert_manual(
                tenant_a.id, day,
                StoreDailyManualUpdate(zhitongche_spend=Decimal("50.00")),
                ops,
            )
            row = await svc.upsert_manual(
                tenant_a.id, day,
                StoreDailyManualUpdate(zhitongche_spend=Decimal("80.00")),
                ops,
            )
            await session.commit()
            assert row.zhitongche_spend == Decimal("80.00")
        finally:
            tenant_id_ctx.reset(tok)


class TestProduction:
    async def test_cross_table_and_week_over_week(
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
            pp = await _platform_product(session, tenant_a, style)
            cur = date(2026, 5, 20)
            prev = date(2026, 5, 19)  # 上一周期（跨度 0 天 → 前一天）
            # 本期：支付 1000 退款 100 + 广告 200 + promotion 500
            await _qianniu(
                session, tenant_a, pp, cur, pay="1000.00",
                extra={"refund_amount": "100.00", "add_cart_count": 50},
            )
            await _ad(session, tenant_a, pp, cur, cost="200.00")
            await promotion_factory.promotion(
                style=style, blogger=blogger, pr=pr,
                cooperation_date=cur, quote_amount=Decimal("500.00"),
            )
            # 上期：支付 800
            await _qianniu(session, tenant_a, pp, prev, pay="800.00")
            await session.commit()

            report = await ProductionService(session).get_report(
                tenant_a.id, (cur, cur)
            )
            assert len(report.items) == 1
            row = report.items[0]
            assert row.pay_amount == Decimal("1000.00")
            assert row.refund_amount == Decimal("100.00")
            assert row.confirmed_amount == Decimal("900.00")
            assert row.total_spend == Decimal("700.00")  # 200 ad + 500 promo
            # net_roi = 900 / 700
            assert row.net_roi == Decimal("1.2857")
            assert row.return_rate == Decimal("0.1000")
            # 上一周期独立计算
            assert report.previous is not None
            prev_rows = [p for p in report.previous if p.style_id == style.id]
            assert prev_rows and prev_rows[0].pay_amount == Decimal("800.00")
        finally:
            tenant_id_ctx.reset(tok)


class TestRls:
    async def test_work_progress_tenant_isolation(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_b.id)
        try:
            pr_b = await factory.user(tenant_b, roles=[pr_role])
            style_b = await product_factory.style(tenant=tenant_b)
            blogger_b = await blogger_factory.blogger(tenant=tenant_b)
            await promotion_factory.promotion(
                style=style_b, blogger=blogger_b, pr=pr_b, tenant=tenant_b,
                cooperation_date=date(2026, 5, 10),
            )
            await session.commit()
            # 查 tenant_a 的工作进度 → 不含 tenant_b 数据
            rows_a = await WorkProgressService(session).get_for_month(
                tenant_a.id, "2026-05"
            )
            assert all(r.pr_id != pr_b.id for r in rows_a)
        finally:
            tenant_id_ctx.reset(tok)
