"""U11 集成测试：博主质量聚合 + 批量重算。

覆盖 services/metric/blogger_quality 聚合 + BloggerTagService.recompute_for_tenant
+ BloggerService.recompute_tags_for_current_tenant 端到端。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.service import BloggerService
from app.modules.blogger.tag_config import TAG_BESTSELLER, TAG_HIGH_VALUE
from app.services.metric.blogger_quality import (
    avg_cpl_for_blogger,
    compute_quality_tags,
    hit_rate_for_blogger,
)

pytestmark = pytest.mark.asyncio


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
            # quote=500, like=1000, 小红书系数1.0 → effective=1000 → cpl=0.5
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                quote_amount=Decimal("500.00"),
                like_count=1000,
                platform="小红书",
            )
            cpl = await avg_cpl_for_blogger(blogger.id, session, tenant_a.id)
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
            await promotion_factory.promotion(
                style=style, blogger=blogger, like_count=None
            )
            assert await avg_cpl_for_blogger(blogger.id, session, tenant_a.id) is None
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
                await promotion_factory.promotion(
                    style=style, blogger=blogger, like_count=lc
                )
            rate = await hit_rate_for_blogger(blogger.id, session, tenant_a.id)
            assert rate == Decimal("0.5000")
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
            assert await hit_rate_for_blogger(blogger.id, session, tenant_a.id) is None
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
            # 低 CPL + 爆文 → 高性价比 + 带货型
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                quote_amount=Decimal("500.00"),
                like_count=2000,
                platform="小红书",
            )
            tags = await compute_quality_tags(blogger.id, session, tenant_a.id)
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
            blogger = await blogger_factory.blogger(
                follower_count=200_000, blogger_type="素人"
            )
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                quote_amount=Decimal("500.00"),
                like_count=2000,
                platform="小红书",
            )
            user = await factory.user(tenant_a, roles=[admin_role])
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
                audience_profile={
                    "note_stats": {"avg_likes": 5, "avg_reads": 10_000}
                },
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
