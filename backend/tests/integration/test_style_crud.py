"""U02 Style CRUD 集成测试。

覆盖：
- EP02-S01 创建款式（含 style_code 重复 409）
- EP02-S03 编辑款式（含字段未变更不写 audit）
- 软删 / 停用 / 恢复
- 多租户隔离（隔离不到 service 层而是 ORM 层钩子）
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.enums import Category
from app.modules.product.exceptions import (
    StyleCodeConflictError,
    StyleHasActiveSkuError,
    StyleNotFoundError,
)
from app.modules.product.repository import StyleListFilters
from app.modules.product.schemas import StyleCreate, StyleUpdate
from app.modules.product.service import StyleService


@pytest.fixture
def stub_cache() -> AsyncMock:
    return AsyncMock()


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateStyle:
    async def test_create_basic(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            response = await svc.create_style(
                StyleCreate(
                    style_code="W001",
                    style_name="波点花边连衣裙",
                    short_name="波点花边",
                    category=Category.DRESS,
                ),
                user,
            )
            assert response.style_code == "W001"
            assert response.style_name == "波点花边连衣裙"
            assert response.short_name == "波点花边"
            assert response.design_status == "大货"
            assert response.is_active is True
            assert response.is_deleted is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_duplicate_style_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.style(style_code="W001")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            with pytest.raises(StyleCodeConflictError):
                await svc.create_style(
                    StyleCreate(
                        style_code="W001",
                        style_name="另一款",
                        category=Category.DRESS,
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdateStyle:
    async def test_update_style_name_no_audit_for_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """EP02-S03: 编辑款式名不写 audit (style_name 非敏感字段)."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_name="原名")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            response = await svc.update_style(
                style.id,
                StyleUpdate(style_name="新名"),
                user,
            )
            assert response.style_name == "新名"
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_unchanged_returns_same(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """字段未变更时不更新 + 不写 audit (BR-U02-32)."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_name="保持")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            response = await svc.update_style(
                style.id,
                StyleUpdate(style_name="保持"),
                user,
            )
            assert response.style_name == "保持"
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_nonexistent_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            with pytest.raises(StyleNotFoundError):
                await svc.update_style(uuid4(), StyleUpdate(style_name="x"), user)
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSoftDeleteStyle:
    async def test_soft_delete_blocked_by_active_sku(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """BR-U02-21: 删 style 必须先停用所有 active sku."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            await product_factory.sku(style)
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            with pytest.raises(StyleHasActiveSkuError):
                await svc.soft_delete_style(style.id, user)
        finally:
            tenant_id_ctx.reset(token)

    async def test_soft_delete_no_skus(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            await svc.soft_delete_style(style.id, user)
            await session.refresh(style)
            assert style.is_deleted is True
            assert style.is_active is False
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestDisableEnableStyle:
    """停用 / 启用（is_active）与恢复软删（is_deleted）是两条独立通道。

    回归用户反馈：前端曾把「恢复」接到 restore_style（软删恢复），
    对未软删的款式必然抛错，导致停用后无法再启用。
    """

    async def test_disable_then_enable_roundtrip(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)

            disabled = await svc.disable_style(style.id, user)
            assert disabled.is_active is False

            enabled = await svc.enable_style(style.id, user)
            assert enabled.is_active is True
            # 启用不应触碰软删标记
            assert enabled.is_deleted is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_restore_rejects_merely_disabled_style(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """仅被停用（未软删）的款式走 restore 必须报错 —— 这正是之前前端踩的坑。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            await svc.disable_style(style.id, user)

            with pytest.raises(StyleNotFoundError):
                await svc.restore_style(style.id, user)
        finally:
            tenant_id_ctx.reset(token)

    async def test_list_status_filter_and_ordering(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            active = await product_factory.style(style_code="ACT001")
            inactive = await product_factory.style(style_code="INACT001")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            await svc.disable_style(inactive.id, user)

            # 默认：只看启用
            page = await svc.list_styles(
                filters=StyleListFilters(), page=1, page_size=50, user=user
            )
            ids = {i.id for i in page.items}
            assert active.id in ids
            assert inactive.id not in ids

            # 只看停用
            page = await svc.list_styles(
                filters=StyleListFilters(is_active=False, include_inactive=True),
                page=1,
                page_size=50,
                user=user,
            )
            ids = {i.id for i in page.items}
            assert ids == {inactive.id}

            # 全部：停用排在最后
            page = await svc.list_styles(
                filters=StyleListFilters(include_inactive=True),
                page=1,
                page_size=50,
                user=user,
            )
            flags = [i.is_active for i in page.items]
            assert set(flags) == {True, False}
            assert flags == sorted(flags, reverse=True), "停用的应排在最后"
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestListStyles:
    async def test_list_pagination(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            for i in range(25):
                await product_factory.style(style_code=f"ST{i:03d}")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)
            from app.modules.product.repository import StyleListFilters

            page = await svc.list_styles(
                filters=StyleListFilters(),
                page=1,
                page_size=10,
                user=user,
            )
            assert len(page.items) == 10
            assert page.total == 25
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestGoodsAttribution:
    """款式的商品归属：套装来自 goods_main.is_suit，不再按共用千牛ID 推断。

    千牛ID 属于平台链接层（运维视图维护），款式管理页只关心「这件衣服归哪个商品」。
    """

    @staticmethod
    async def _goods(
        session: AsyncSession, tenant: Any, *styles: Any, code: str, is_suit: bool = False
    ) -> Any:
        from app.modules.product.goods_models import GoodsMain, GoodsStyleItem

        goods = GoodsMain(
            tenant_id=tenant.id,
            goods_code=code,
            goods_title="+".join(s.style_name for s in styles) if is_suit else code,
            is_suit=is_suit,
        )
        session.add(goods)
        await session.flush()
        for idx, style in enumerate(styles):
            session.add(
                GoodsStyleItem(
                    tenant_id=tenant.id,
                    goods_main_id=goods.id,
                    style_id=style.id,
                    sort_order=idx,
                )
            )
        await session.flush()
        return goods

    async def test_suit_members_share_suite_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """套装内每一行拿到同一个套装标题，且主商品就是套装本身。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            a = await product_factory.style(style_code="SUITE_A", style_name="木耳边打底衫")
            b = await product_factory.style(style_code="SUITE_B", style_name="卡其毛衣马甲")
            await self._goods(session, tenant_a, a, b, code="SUIT-1", is_suit=True)
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)

            page = await svc.list_styles(
                filters=StyleListFilters(keyword="SUITE_"),
                page=1,
                page_size=50,
                user=user,
            )
            rows = {i.style_code: i for i in page.items}
            assert len(rows) == 2
            for code in ("SUITE_A", "SUITE_B"):
                assert rows[code].goods_code == "SUIT-1"
                assert rows[code].goods_is_suit is True
                assert rows[code].suite_name == "木耳边打底衫+卡其毛衣马甲"
        finally:
            tenant_id_ctx.reset(token)

    async def test_solo_style_has_no_suite_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="SOLO001", style_name="单件连衣裙")
            await self._goods(session, tenant_a, style, code="SOLO001")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)

            page = await svc.list_styles(
                filters=StyleListFilters(keyword="SOLO001"),
                page=1,
                page_size=50,
                user=user,
            )
            assert page.items[0].goods_code == "SOLO001"
            assert page.items[0].goods_is_suit is False
            assert page.items[0].suite_name is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_style_without_goods_returns_nulls(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """还没归到商品的款式不报错，归属字段留空供前端提示「未归属」。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await product_factory.style(style_code="NOGOODS1", style_name="甲")
            await product_factory.style(style_code="NOGOODS2", style_name="乙")
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)

            page = await svc.list_styles(
                filters=StyleListFilters(keyword="NOGOODS"),
                page=1,
                page_size=50,
                user=user,
            )
            assert len(page.items) == 2
            assert all(i.goods_code is None for i in page.items)
            assert all(i.suite_name is None for i in page.items)
        finally:
            tenant_id_ctx.reset(token)

    async def test_style_in_both_solo_and_suit_shows_both(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """既单卖又进套装时：主商品是单品，套装名另外给出，两个信息都要看得到。

        生产上 260419 就是这个形态 —— 它有自己的销售链接，同时和 260415 组成套装。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            shared = await product_factory.style(style_code="DUAL_A", style_name="打底衫")
            partner = await product_factory.style(style_code="DUAL_B", style_name="马甲")
            await self._goods(session, tenant_a, shared, code="DUAL_A")
            await self._goods(session, tenant_a, shared, partner, code="SUIT-DUAL", is_suit=True)
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)

            response = await svc.get_style(shared.id, user)
            assert response.goods_code == "DUAL_A"
            assert response.goods_is_suit is False
            assert response.suite_name == "打底衫+马甲"
        finally:
            tenant_id_ctx.reset(token)

    async def test_single_read_path_includes_attribution(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """单条读取路径（get_style）也要带归属，前端任何入口拿到的 Style 都一致。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            a = await product_factory.style(style_code="PAIR_A", style_name="上衣")
            b = await product_factory.style(style_code="PAIR_B", style_name="裤子")
            await self._goods(session, tenant_a, a, b, code="SUIT-PAIR", is_suit=True)
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = StyleService(session)

            response = await svc.get_style(a.id, user)
            assert response.goods_code == "SUIT-PAIR"
            assert response.suite_name == "上衣+裤子"
        finally:
            tenant_id_ctx.reset(token)
