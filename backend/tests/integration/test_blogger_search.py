"""U03 Blogger 搜索集成测试 + 防侧信道关键测试.

覆盖：
- EP04-S03 多筛选搜索（关键字 + 范围 + tag）
- 字段读权限矩阵（4 角色）
- **P-U03-01 防侧信道**：wechat 在无 CONTACT_VISIBLE_ROLES 时不参与 keyword 匹配
- 系统失败让异常冒泡（不伪装空数组）
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.repository import BloggerListFilters
from app.modules.blogger.service import BloggerService


@pytest.mark.integration
@pytest.mark.asyncio
class TestKeywordSearch:
    async def test_match_nickname(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await blogger_factory.blogger(nickname="时尚博主A")
            await blogger_factory.blogger(nickname="美食博主B")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            page = await svc.list_bloggers(
                filters=BloggerListFilters(keyword="时尚"),
                page=1,
                page_size=20,
                user=user,
            )
            assert page.total == 1
            assert page.items[0].nickname == "时尚博主A"
        finally:
            tenant_id_ctx.reset(token)

    async def test_match_xiaohongshu_id(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await blogger_factory.blogger(xiaohongshu_id="UNIQUE001")
            await blogger_factory.blogger(xiaohongshu_id="OTHER002")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            page = await svc.list_bloggers(
                filters=BloggerListFilters(keyword="UNIQUE"),
                page=1,
                page_size=20,
                user=user,
            )
            assert page.total == 1
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSideChannelProtection:
    """P-U03-01 防侧信道关键测试."""

    async def test_pr_can_match_wechat(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        blogger_factory: Any,
    ) -> None:
        """PR 角色（CONTACT_VISIBLE_ROLES）keyword 命中 wechat."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await blogger_factory.blogger(
                nickname="不相关昵称",
                xiaohongshu_id="XHS_OTHER",
                wechat="wx_secret_123",
            )
            user = await factory.user(tenant_a, roles=[pr_role])
            svc = BloggerService(session)
            page = await svc.list_bloggers(
                filters=BloggerListFilters(keyword="secret"),
                page=1,
                page_size=20,
                user=user,
            )
            # PR 有 CONTACT_VISIBLE 权限，wechat 参与匹配 → 命中
            assert page.total == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_designer_cannot_match_wechat(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        designer_role: Any,
        blogger_factory: Any,
    ) -> None:
        """designer 角色（无 CONTACT_VISIBLE）keyword 不能通过 wechat 命中."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await blogger_factory.blogger(
                nickname="不相关昵称",
                xiaohongshu_id="XHS_OTHER",
                wechat="wx_secret_designer_test",
            )
            user = await factory.user(tenant_a, roles=[designer_role])
            svc = BloggerService(session)
            page = await svc.list_bloggers(
                filters=BloggerListFilters(keyword="secret_designer"),
                page=1,
                page_size=20,
                user=user,
            )
            # designer 无 CONTACT_VISIBLE 权限，wechat 不参与匹配 → 不命中
            assert page.total == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_finance_cannot_match_wechat(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        blogger_factory: Any,
    ) -> None:
        """finance 角色虽可读 quote 但无 CONTACT_VISIBLE，wechat 不参与匹配."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await blogger_factory.blogger(
                nickname="不相关昵称",
                xiaohongshu_id="XHS_OTHER",
                wechat="wx_secret_finance_test",
            )
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BloggerService(session)
            page = await svc.list_bloggers(
                filters=BloggerListFilters(keyword="secret_finance"),
                page=1,
                page_size=20,
                user=user,
            )
            assert page.total == 0
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestFieldVisibilityInResponse:
    """字段读权限矩阵."""

    async def test_pr_sees_quote_and_contact(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger(
                quote=Decimal("500.00"),
                wechat="wx_test",
                phone="13800000000",
            )
            user = await factory.user(tenant_a, roles=[pr_role])
            svc = BloggerService(session)
            response = await svc.get_blogger(blogger.id, user)
            assert response.quote == Decimal("500.00")
            assert response.wechat == "wx_test"
            assert response.phone == "13800000000"
        finally:
            tenant_id_ctx.reset(token)

    async def test_finance_sees_quote_only(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger(
                quote=Decimal("500.00"),
                wechat="wx_test",
                phone="13800000000",
            )
            user = await factory.user(tenant_a, roles=[finance_role])
            svc = BloggerService(session)
            response = await svc.get_blogger(blogger.id, user)
            # finance 见 quote 不见 contact
            assert response.quote == Decimal("500.00")
            assert response.wechat is None
            assert response.phone is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_designer_sees_neither(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        designer_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger(
                quote=Decimal("500.00"),
                wechat="wx_test",
                phone="13800000000",
            )
            user = await factory.user(tenant_a, roles=[designer_role])
            svc = BloggerService(session)
            response = await svc.get_blogger(blogger.id, user)
            assert response.quote is None
            assert response.wechat is None
            assert response.phone is None
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestRangeAndTagFilters:
    async def test_follower_count_range(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await blogger_factory.blogger(follower_count=500)
            await blogger_factory.blogger(follower_count=5000)
            await blogger_factory.blogger(follower_count=50000)
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            page = await svc.list_bloggers(
                filters=BloggerListFilters(
                    follower_count_min=1000, follower_count_max=10000
                ),
                page=1,
                page_size=20,
                user=user,
            )
            assert page.total == 1
            assert page.items[0].follower_count == 5000
        finally:
            tenant_id_ctx.reset(token)

    async def test_category_tag_filter(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await blogger_factory.blogger(category_tags=["穿搭", "美妆"])
            await blogger_factory.blogger(category_tags=["美食"])
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            page = await svc.list_bloggers(
                filters=BloggerListFilters(category_tag="穿搭"),
                page=1,
                page_size=20,
                user=user,
            )
            assert page.total == 1
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSystemFailureNotMaskedAsEmpty:
    """match 降级语义：系统失败让异常冒泡，不伪装空数组."""

    async def test_db_error_propagates(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            from app.modules.blogger.repository import BloggerRepository

            with patch.object(
                BloggerRepository,
                "list",
                new=AsyncMock(side_effect=RuntimeError("simulated DB outage")),
            ), pytest.raises(RuntimeError, match="simulated DB outage"):
                await svc.list_bloggers(
                    filters=BloggerListFilters(keyword="anything"),
                    page=1,
                    page_size=20,
                    user=user,
                )
        finally:
            tenant_id_ctx.reset(token)
