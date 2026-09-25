"""商品分层（goods_main / goods_style_item）结构与约束。

对齐 PRD V1.4 第 3 章。这里守的是分层本身的不变量：
- 一个款式可以属于多个商品（一款单卖 + 进套装），一个商品可以含多个款式（套装）
- 同一商品内同一款式不能重复挂
- 商品编码租户内唯一
- 一个商品可以挂普通与直播两条链接，同一千牛ID 只能属于一个商品
- 套装样品成本 = 启用关联行的单件成本之和（停用行不参与）
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _goods(
    session: AsyncSession, tenant: Any, code: str, *, title: str = "商品", is_suit: bool = False
) -> GoodsMain:
    g = GoodsMain(
        tenant_id=tenant.id,
        goods_code=code,
        goods_title=title,
        is_suit=is_suit,
    )
    session.add(g)
    await session.flush()
    return g


async def _link(
    session: AsyncSession,
    tenant: Any,
    goods: GoodsMain,
    style: Any,
    platform_id: str,
    *,
    channel: str = "普通",
    platform: str = "千牛",
) -> PlatformProduct:
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform=platform,
        platform_id=platform_id,
        goods_main_id=goods.id,
        channel=channel,
        style_id=style.id,
    )
    session.add(pp)
    await session.flush()
    return pp


class TestGoodsStyleRelation:
    async def test_suit_holds_multiple_styles(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            a = await product_factory.style(style_code="GS_A", style_name="上衣")
            b = await product_factory.style(style_code="GS_B", style_name="裤子")
            suit = await _goods(session, tenant_a, "SUIT-1", title="上衣+裤子", is_suit=True)
            for idx, st in enumerate((a, b)):
                session.add(
                    GoodsStyleItem(
                        tenant_id=tenant_a.id,
                        goods_main_id=suit.id,
                        style_id=st.id,
                        single_goods_cost=Decimal("50.00"),
                        sort_order=idx,
                    )
                )
            await session.flush()

            count = (
                await session.execute(
                    select(func.count())
                    .select_from(GoodsStyleItem)
                    .where(GoodsStyleItem.goods_main_id == suit.id)
                )
            ).scalar_one()
            assert count == 2
        finally:
            tenant_id_ctx.reset(tok)

    async def test_style_can_belong_to_multiple_goods(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """一件衣服既单卖又进套装 —— 这正是需要关联表而非单个外键的原因。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="GS_SHARED")
            solo = await _goods(session, tenant_a, "SOLO-1")
            suit = await _goods(session, tenant_a, "SUIT-2", is_suit=True)
            for g in (solo, suit):
                session.add(
                    GoodsStyleItem(tenant_id=tenant_a.id, goods_main_id=g.id, style_id=style.id)
                )
            await session.flush()

            count = (
                await session.execute(
                    select(func.count())
                    .select_from(GoodsStyleItem)
                    .where(GoodsStyleItem.style_id == style.id)
                )
            ).scalar_one()
            assert count == 2
        finally:
            tenant_id_ctx.reset(tok)

    async def test_same_style_twice_in_one_goods_rejected(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="GS_DUP")
            goods = await _goods(session, tenant_a, "DUP-1")
            session.add(
                GoodsStyleItem(tenant_id=tenant_a.id, goods_main_id=goods.id, style_id=style.id)
            )
            await session.flush()
            session.add(
                GoodsStyleItem(tenant_id=tenant_a.id, goods_main_id=goods.id, style_id=style.id)
            )
            with pytest.raises(IntegrityError):
                await session.flush()
        finally:
            await session.rollback()
            tenant_id_ctx.reset(tok)

    async def test_goods_code_unique_per_tenant(self, session: AsyncSession, tenant_a: Any) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _goods(session, tenant_a, "UNIQ-1")
            session.add(GoodsMain(tenant_id=tenant_a.id, goods_code="UNIQ-1", goods_title="重复"))
            with pytest.raises(IntegrityError):
                await session.flush()
        finally:
            await session.rollback()
            tenant_id_ctx.reset(tok)


class TestSuitCost:
    async def test_inactive_item_excluded_from_sum(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """PRD：套装成本只统计启用的子表记录。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            a = await product_factory.style(style_code="GC_A")
            b = await product_factory.style(style_code="GC_B")
            suit = await _goods(session, tenant_a, "SUIT-COST", is_suit=True)
            session.add(
                GoodsStyleItem(
                    tenant_id=tenant_a.id,
                    goods_main_id=suit.id,
                    style_id=a.id,
                    single_goods_cost=Decimal("60.00"),
                    is_active=True,
                )
            )
            session.add(
                GoodsStyleItem(
                    tenant_id=tenant_a.id,
                    goods_main_id=suit.id,
                    style_id=b.id,
                    single_goods_cost=Decimal("40.00"),
                    is_active=False,
                )
            )
            await session.flush()

            total = (
                await session.execute(
                    select(func.coalesce(func.sum(GoodsStyleItem.single_goods_cost), 0)).where(
                        GoodsStyleItem.goods_main_id == suit.id,
                        GoodsStyleItem.is_active.is_(True),
                    )
                )
            ).scalar_one()
            assert total == Decimal("60.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_negative_cost_rejected(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="GC_NEG")
            goods = await _goods(session, tenant_a, "NEG-1")
            session.add(
                GoodsStyleItem(
                    tenant_id=tenant_a.id,
                    goods_main_id=goods.id,
                    style_id=style.id,
                    single_goods_cost=Decimal("-1.00"),
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
        finally:
            await session.rollback()
            tenant_id_ctx.reset(tok)


class TestChannelLinks:
    async def test_goods_can_hold_normal_and_live_links(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """PRD 改动 1：同一商品可同时挂普通链接与直播链接，用于分账。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="CH_A")
            goods = await _goods(session, tenant_a, "CH-1")
            await _link(session, tenant_a, goods, style, "QN_NORMAL", channel="普通")
            await _link(session, tenant_a, goods, style, "QN_LIVE", channel="直播")

            rows = (
                (
                    await session.execute(
                        select(PlatformProduct.channel).where(
                            PlatformProduct.goods_main_id == goods.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert sorted(rows) == ["普通", "直播"]
        finally:
            tenant_id_ctx.reset(tok)

    async def test_invalid_channel_rejected(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="CH_BAD")
            goods = await _goods(session, tenant_a, "CH-BAD")
            session.add(
                PlatformProduct(
                    tenant_id=tenant_a.id,
                    platform="千牛",
                    platform_id="QN_BAD",
                    goods_main_id=goods.id,
                    channel="团购",
                    style_id=style.id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
        finally:
            await session.rollback()
            tenant_id_ctx.reset(tok)

    async def test_same_platform_id_cannot_serve_two_goods(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """一个千牛ID 就是一个销售链接，不能同时属于两个商品。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="CH_DUP")
            g1 = await _goods(session, tenant_a, "CH-DUP-1")
            g2 = await _goods(session, tenant_a, "CH-DUP-2")
            await _link(session, tenant_a, g1, style, "QN_SHARED")
            session.add(
                PlatformProduct(
                    tenant_id=tenant_a.id,
                    platform="千牛",
                    platform_id="QN_SHARED",
                    goods_main_id=g2.id,
                    style_id=style.id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.flush()
        finally:
            await session.rollback()
            tenant_id_ctx.reset(tok)

    async def test_channel_defaults_to_normal(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code="CH_DEF")
            goods = await _goods(session, tenant_a, "CH-DEF")
            pp = PlatformProduct(
                tenant_id=tenant_a.id,
                platform="千牛",
                platform_id="QN_DEF",
                goods_main_id=goods.id,
                style_id=style.id,
            )
            session.add(pp)
            await session.flush()
            await session.refresh(pp)
            assert pp.channel == "普通"
        finally:
            tenant_id_ctx.reset(tok)


class TestSoftDelete:
    async def test_delete_records_who_and_when(
        self, session: AsyncSession, tenant_a: Any, factory: Any, admin_role: Any
    ) -> None:
        """PRD 改动 1：商品支持软删除，且要记录谁删、何时删。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            goods = await _goods(session, tenant_a, "DEL-1")
            goods.is_deleted = True
            goods.deleted_by = user.id
            goods.deleted_at = func.now()
            await session.flush()
            await session.refresh(goods)
            assert goods.is_deleted is True
            assert goods.deleted_by == user.id
            assert goods.deleted_at is not None
        finally:
            tenant_id_ctx.reset(tok)
