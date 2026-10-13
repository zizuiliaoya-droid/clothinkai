"""U11 集成测试：博主质量聚合 + 批量重算。

覆盖 services/metric/blogger_quality 聚合 + BloggerTagService.recompute_for_tenant
+ BloggerService.recompute_tags_for_current_tenant 端到端。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.service import BloggerService
from app.modules.blogger.tag_config import TAG_BESTSELLER, TAG_HIGH_VALUE
from app.modules.metric_config.models import PromotionPlatformSetting
from app.modules.metric_config.service import PlatformMetricSettingService
from app.modules.metric_config.settings import PlatformMetricSettings
from app.services.metric.blogger_quality import (
    avg_cpl_for_blogger,
    compute_quality_tags,
    hit_rate_for_blogger,
)

pytestmark = pytest.mark.asyncio

_DEFAULTS = PlatformMetricSettings.defaults()


class TestBloggerQualityAggregation:
    async def test_avg_cpl_low_cost(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger(follower_count=50_000)
            # 总推广成本 = 服务费 400 + 样品 80 + 寄回运费 20 = 500；like=1000、小红书系数 1.0
            # → effective=1000 → cpl=0.5。录过 7 天数据才算。
            p = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                quote_amount=Decimal("400.00"),
                cost_snapshot=Decimal("80.00"),
                return_shipping_fee=Decimal("20.00"),
                like_count=1000,
                platform="小红书",
            )
            # 工厂的 kwarg 清单里没有 metrics_recorded_at，直接写
            p.metrics_recorded_at = datetime.now(UTC)
            # 再加一张有点赞但没录 7 天数据的：不能拉低 / 拉高平均值
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                quote_amount=Decimal("10.00"),
                like_count=1000,
                platform="小红书",
            )
            await session.flush()
            cpl = await avg_cpl_for_blogger(blogger.id, session, tenant_a.id, settings=_DEFAULTS)
            assert cpl == Decimal("0.5000")
        finally:
            tenant_id_ctx.reset(token)

    async def test_avg_cpl_none_when_no_likes(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger(follower_count=50_000)
            await promotion_factory.promotion(style=style, blogger=blogger, like_count=None)
            assert (
                await avg_cpl_for_blogger(blogger.id, session, tenant_a.id, settings=_DEFAULTS)
                is None
            )
        finally:
            tenant_id_ctx.reset(token)

    async def test_hit_rate(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger(follower_count=50_000)
            # 2 篇爆文(>=1000) + 2 篇普通 → hit_rate=0.5
            for lc in (2000, 1500, 100, 50):
                await promotion_factory.promotion(style=style, blogger=blogger, like_count=lc)
            rate = await hit_rate_for_blogger(blogger.id, session, tenant_a.id, settings=_DEFAULTS)
            assert rate == Decimal("0.5000")
        finally:
            tenant_id_ctx.reset(token)

    async def test_bestseller_uses_platform_threshold(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """7e R7：「带货型」的爆文按平台阈值判。抖音 1500 赞：默认阈值 10000 不算，配 1000 后算。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger(follower_count=50_000)
            await promotion_factory.promotion(
                style=style, blogger=blogger, like_count=1500, platform="抖音"
            )
            svc = PlatformMetricSettingService(session)
            before = await compute_quality_tags(
                blogger.id, session, tenant_a.id, settings=await svc.get_settings(tenant_a.id)
            )
            session.add(
                PromotionPlatformSetting(
                    tenant_id=tenant_a.id,
                    platform="抖音",
                    like_coefficient=Decimal("0.100"),
                    hit_threshold=1000,
                )
            )
            await session.flush()
            after = await compute_quality_tags(
                blogger.id, session, tenant_a.id, settings=await svc.get_settings(tenant_a.id)
            )
            assert TAG_BESTSELLER not in before
            assert TAG_BESTSELLER in after
        finally:
            tenant_id_ctx.reset(token)

    async def test_hit_rate_none_no_promotions(
        self,
        session: AsyncSession,
        tenant_a: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger(follower_count=50_000)
            assert (
                await hit_rate_for_blogger(blogger.id, session, tenant_a.id, settings=_DEFAULTS)
                is None
            )
        finally:
            tenant_id_ctx.reset(token)

    async def test_quality_tags_both(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger(follower_count=50_000)
            # 低 CPL + 爆文 → 高性价比 + 带货型（CPL 要录过 7 天数据才算）
            p = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                quote_amount=Decimal("500.00"),
                like_count=2000,
                platform="小红书",
            )
            p.metrics_recorded_at = datetime.now(UTC)
            await session.flush()
            tags = await compute_quality_tags(blogger.id, session, tenant_a.id, settings=_DEFAULTS)
            assert TAG_HIGH_VALUE in tags
            assert TAG_BESTSELLER in tags
        finally:
            tenant_id_ctx.reset(token)


class TestRecomputeForTenant:
    async def test_recompute_sets_type_and_tags(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            # KOL（粉丝量），手动 blogger_type 故意错置为素人 → 重算应纠正
            blogger = await blogger_factory.blogger(follower_count=200_000, blogger_type="素人")
            p = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                quote_amount=Decimal("500.00"),
                like_count=2000,
                platform="小红书",
            )
            p.metrics_recorded_at = datetime.now(UTC)
            await session.flush()
            await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            result = await svc.recompute_tags_for_current_tenant(tenant_a.id)
            assert result["updated"] >= 1
            assert result["failed"] == 0

            await session.refresh(blogger)
            assert blogger.blogger_type == "KOL"
            assert TAG_HIGH_VALUE in blogger.quality_tags
            assert TAG_BESTSELLER in blogger.quality_tags
        finally:
            tenant_id_ctx.reset(token)

    async def test_recompute_fake_detection(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            # 低互动 audience_profile → 假号嫌疑
            blogger = await blogger_factory.blogger(
                follower_count=50_000,
                audience_profile={"note_stats": {"avg_likes": 5, "avg_reads": 10_000}},
            )
            svc = BloggerService(session)
            await svc.recompute_tags_for_current_tenant(tenant_a.id)
            await session.refresh(blogger)
            assert blogger.is_suspected_fake is True
        finally:
            tenant_id_ctx.reset(token)


class TestRecomputeOnCreateUpdate:
    async def test_create_auto_type(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        from app.modules.blogger.schemas import BloggerCreate

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            resp = await svc.create_blogger(
                BloggerCreate(
                    xiaohongshu_id="U11AUTO",
                    nickname="自动分级",
                    follower_count=150_000,
                ),
                user,
            )
            assert resp.blogger_type == "KOL"
        finally:
            tenant_id_ctx.reset(token)
