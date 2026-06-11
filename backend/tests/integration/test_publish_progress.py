"""U08 集成测试：发文进度看板聚合（EP09-S01）。

构造已知 promotion 数据集 → 断言 summary 9 指标 / cards / detail / 空集 null / 多租户隔离。
session 走 bypass 引擎（RLS off）；repository 显式 tenant_id 过滤保证隔离正确。
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.domain import resolve_time_range
from app.modules.report.service import PublishProgressService


async def _seed_dataset(
    product_factory, blogger_factory, promotion_factory, factory, tenant
):
    """4 promotion（已发布×2 / 超时未发布×1 / 已取消×1），同 style 同 blogger。"""
    today = get_today()
    style = await product_factory.style(
        style_name="连衣裙X", short_name="裙X", tenant=tenant
    )
    blogger = await blogger_factory.blogger(nickname="小美", tenant=tenant)
    pr = await factory.user(tenant)
    common = dict(
        style=style, blogger=blogger, pr=pr, tenant=tenant,
        cooperation_date=today,
    )
    # 已发布，小红书 like 500（折算 500）
    await promotion_factory.promotion(
        **common, quote_amount=Decimal("1000.00"),
        publish_status="已发布", like_count=500, platform="小红书",
    )
    # 已发布，抖音 like 100（折算 10）
    await promotion_factory.promotion(
        **common, quote_amount=Decimal("2000.00"),
        publish_status="已发布", like_count=100, platform="抖音",
    )
    # 未发布 + scheduled 过去 5 天 → 超时
    await promotion_factory.promotion(
        **common, quote_amount=Decimal("500.00"),
        publish_status="未发布",
        scheduled_publish_date=today - timedelta(days=5),
    )
    # 已取消
    await promotion_factory.promotion(
        **common, quote_amount=Decimal("300.00"), publish_status="已取消",
    )
    return style, pr


@pytest.mark.integration
@pytest.mark.asyncio
class TestPublishProgress:
    async def test_summary_metrics(
        self, session: Any, tenant_a, product_factory, blogger_factory,
        promotion_factory, factory
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_dataset(
                product_factory, blogger_factory, promotion_factory, factory,
                tenant_a,
            )
            tr = resolve_time_range("last_30d")
            summary = await PublishProgressService(session).get_summary(
                tenant_a.id, tr
            )
            assert summary.quote_count == 4
            assert summary.quote_amount == Decimal("3800.00")
            assert summary.cooperation_amount == Decimal("3000.00")
            assert summary.publish_count == 2
            assert summary.cancel_count == 1
            assert summary.overdue_count == 1
            assert summary.like_count == 510  # 500 + 100*0.1
            assert summary.publish_rate == Decimal("0.5000")
            assert summary.publish_rate_level == "yellow"
            assert summary.overdue_rate == Decimal("0.2500")
            # cpl = 3000 / 510
            assert summary.cpl is not None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_cards_group_by_style(
        self, session: Any, tenant_a, product_factory, blogger_factory,
        promotion_factory, factory
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, _ = await _seed_dataset(
                product_factory, blogger_factory, promotion_factory, factory,
                tenant_a,
            )
            tr = resolve_time_range("last_30d")
            page = await PublishProgressService(session).get_cards(
                tenant_a.id, tr, page=1, page_size=20
            )
            assert page.total == 1
            card = page.items[0]
            assert str(card.style_id) == str(style.id)
            assert card.quote_count == 4
            assert card.publish_count == 2
            assert card.overdue_count == 1
            assert card.like_count == 510
        finally:
            tenant_id_ctx.reset(tok)

    async def test_detail_by_pr_and_time(
        self, session: Any, tenant_a, product_factory, blogger_factory,
        promotion_factory, factory
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, pr = await _seed_dataset(
                product_factory, blogger_factory, promotion_factory, factory,
                tenant_a,
            )
            tr = resolve_time_range("last_30d")
            svc = PublishProgressService(session)
            by_pr = await svc.get_detail_by_pr(tenant_a.id, style.id, tr)
            assert len(by_pr) == 1
            assert by_pr[0].quote_count == 4
            assert by_pr[0].publish_count == 2
            by_time = await svc.get_detail_by_time(tenant_a.id, style.id, tr)
            assert len(by_time) >= 1
            assert sum(p.quote_count for p in by_time) == 4
        finally:
            tenant_id_ctx.reset(tok)

    async def test_empty_dataset_null_rates(
        self, session: Any, tenant_a
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            tr = resolve_time_range("last_7d")
            summary = await PublishProgressService(session).get_summary(
                tenant_a.id, tr
            )
            assert summary.quote_count == 0
            assert summary.publish_rate is None
            assert summary.publish_rate_level is None
            assert summary.cpl is None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_tenant_isolation(
        self, session: Any, tenant_a, tenant_b, product_factory,
        blogger_factory, promotion_factory, factory
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_dataset(
                product_factory, blogger_factory, promotion_factory, factory,
                tenant_a,
            )
        finally:
            tenant_id_ctx.reset(tok)
        # 用 tenant_b 视角查询 → 不含 tenant_a 数据
        tr = resolve_time_range("last_30d")
        summary = await PublishProgressService(session).get_summary(
            tenant_b.id, tr
        )
        assert summary.quote_count == 0
