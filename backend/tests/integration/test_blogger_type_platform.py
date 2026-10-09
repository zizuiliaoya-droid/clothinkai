"""8b 博主类型自动分级只对小红书（设计 §3.5，``tag_config.TYPE_GRADED_PLATFORMS``）。

抖音的粉丝量级与小红书不同，阈值没给之前：建 / 改粉丝数 / 单个重算 / 整租户重算都不碰抖音的博主类型；
质量标签与假号照常重算。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.enums import BloggerType, Platform
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate
from app.modules.blogger.service import BloggerService
from app.modules.blogger.tag_config import TYPE_GRADED_PLATFORMS

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.mark.usefixtures("tenant_ctx")
class TestTypeGradingPerPlatform:
    async def test_graded_platforms_is_xiaohongshu_only(self) -> None:
        assert frozenset({"小红书"}) == TYPE_GRADED_PLATFORMS

    async def test_create_douyin_keeps_given_type(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        user = await factory.user(tenant_a, roles=[admin_role])
        svc = BloggerService(session)
        dy = await svc.create_blogger(
            BloggerCreate(
                xiaohongshu_id="T8D1",
                nickname="抖",
                platform=Platform.DOUYIN,
                follower_count=150_000,
                blogger_type=BloggerType.AMATEUR,
            ),
            user,
        )
        xhs = await svc.create_blogger(
            BloggerCreate(
                xiaohongshu_id="T8D1",
                nickname="红",
                follower_count=150_000,
                blogger_type=BloggerType.AMATEUR,
            ),
            user,
        )
        assert dy.blogger_type == "素人"
        assert xhs.blogger_type == "KOL"

    async def test_update_follower_count_douyin_untouched(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        dy = await blogger_factory.blogger(platform="抖音", follower_count=100, blogger_type="素人")
        xhs = await blogger_factory.blogger(follower_count=100, blogger_type="素人")
        user = await factory.user(tenant_a, roles=[admin_role])
        svc = BloggerService(session)
        r_dy = await svc.update_blogger(dy.id, BloggerUpdate(follower_count=500_000), user)
        r_xhs = await svc.update_blogger(xhs.id, BloggerUpdate(follower_count=500_000), user)
        assert r_dy.blogger_type == "素人"
        assert r_xhs.blogger_type == "KOL"

    async def test_recompute_single_douyin_untouched(
        self, session: AsyncSession, blogger_factory: Any
    ) -> None:
        dy = await blogger_factory.blogger(
            platform="抖音", follower_count=500_000, blogger_type="素人"
        )
        xhs = await blogger_factory.blogger(follower_count=500_000, blogger_type="素人")
        svc = BloggerService(session)
        assert (await svc.recompute_blogger_type(dy.id)).blogger_type == "素人"
        assert (await svc.recompute_blogger_type(xhs.id)).blogger_type == "KOL"

    async def test_recompute_tenant_skips_type_but_recomputes_fake(
        self, session: AsyncSession, tenant_a: Any, blogger_factory: Any
    ) -> None:
        low = {"note_stats": {"avg_likes": 5, "avg_reads": 10_000}}
        dy = await blogger_factory.blogger(
            platform="抖音", follower_count=500_000, blogger_type="素人", audience_profile=low
        )
        xhs = await blogger_factory.blogger(follower_count=500_000, blogger_type="素人")
        dy_id, xhs_id = dy.id, xhs.id
        result = await BloggerService(session).recompute_tags_for_current_tenant(tenant_a.id)
        assert result["failed"] == 0
        await session.refresh(dy)
        await session.refresh(xhs)
        assert (dy.id, dy.blogger_type, dy.is_suspected_fake) == (dy_id, "素人", True)
        assert (xhs.id, xhs.blogger_type) == (xhs_id, "KOL")
