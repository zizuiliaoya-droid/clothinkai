"""商品 / 套装管理 CRUD 集成测试。

守的是商品这一层特有的不变量：

- ``is_suit`` 从成员数推导，不由调用方指定 —— 成员改了它必须跟着改
- 没填成本的成员回落到款式 SKU 成本价，套装总成本仍然算得出来
- ``goods_code`` 唯一且软删后不可复用（它是报表与链接归属的引用键）
- 仍挂着平台链接的商品不许删
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.product.goods_repository import GoodsListFilters, GoodsRepository
from app.modules.product.goods_schemas import (
    GoodsMainCreate,
    GoodsMainUpdate,
    GoodsStyleItemIn,
)
from app.modules.product.goods_service import (
    GoodsCodeConflictError,
    GoodsHasLinksError,
    GoodsService,
)
from app.modules.product.platform_product_models import PlatformProduct

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class TestCreateGoods:
    async def test_single_style_is_not_suit(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="G001", style_name="木耳边打底衫")
            svc = GoodsService(session)
            resp = await svc.create(
                GoodsMainCreate(
                    goods_code="G001",
                    goods_title="木耳边打底衫",
                    items=[GoodsStyleItemIn(style_id=style.id, single_goods_cost=Decimal("38.00"))],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            assert resp.is_suit is False
            assert len(resp.items) == 1
            assert resp.items[0].style_code == "G001"
            assert resp.total_cost == Decimal("38.00")
            assert resp.cost_missing_count == 0
            assert resp.link_count == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_two_styles_become_suit_and_sum_cost(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """套装成本 = 成员成本之和。这是套装存在的主要理由。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            vest = await product_factory.style(style_code="S260415", style_name="卡其毛衣马甲")
            top = await product_factory.style(style_code="S260419", style_name="木耳边打底衫")
            svc = GoodsService(session)
            resp = await svc.create(
                GoodsMainCreate(
                    goods_code="SUIT-G1",
                    goods_title="卡其毛衣马甲+木耳边打底衫",
                    items=[
                        GoodsStyleItemIn(style_id=vest.id, single_goods_cost=Decimal("72.50")),
                        GoodsStyleItemIn(style_id=top.id, single_goods_cost=Decimal("38.00")),
                    ],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            assert resp.is_suit is True
            assert resp.total_cost == Decimal("110.50")
            assert resp.cost_missing_count == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_cost_falls_back_to_sku_price(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """不填成本时取款式 SKU 的最高成本价，建档少填一个字段也能算成本。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="G002")
            await product_factory.sku(style, sku_code="G002-R", cost_price=Decimal("55.00"))
            await product_factory.sku(style, sku_code="G002-B", cost_price=Decimal("61.00"))
            svc = GoodsService(session)
            resp = await svc.create(
                GoodsMainCreate(
                    goods_code="G002",
                    goods_title="撞色针织上衣",
                    items=[GoodsStyleItemIn(style_id=style.id)],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            assert resp.items[0].single_goods_cost == Decimal("61.00")
            assert resp.total_cost == Decimal("61.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_style_without_sku_leaves_cost_missing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """款式没有 SKU 时成本留空并计入 cost_missing_count，
        对应生产上那批自动建档、还没补成本的款式。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="G003")
            svc = GoodsService(session)
            resp = await svc.create(
                GoodsMainCreate(
                    goods_code="G003",
                    goods_title="没有 SKU 的款",
                    items=[GoodsStyleItemIn(style_id=style.id)],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            assert resp.items[0].single_goods_cost is None
            assert resp.total_cost is None
            assert resp.cost_missing_count == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_empty_items(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = GoodsService(session)
            with pytest.raises(ValidationError) as exc:
                await svc.create(
                    GoodsMainCreate(goods_code="G004", goods_title="空商品", items=[]),
                    tenant_id=tenant_a.id,
                    user_id=user.id,
                )
            assert exc.value.code == "GOODS_NO_ITEM"
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_duplicate_style(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="G005")
            svc = GoodsService(session)
            with pytest.raises(ValidationError) as exc:
                await svc.create(
                    GoodsMainCreate(
                        goods_code="G005",
                        goods_title="同款挂两次",
                        items=[
                            GoodsStyleItemIn(style_id=style.id),
                            GoodsStyleItemIn(style_id=style.id),
                        ],
                    ),
                    tenant_id=tenant_a.id,
                    user_id=user.id,
                )
            assert exc.value.code == "GOODS_DUPLICATE_STYLE"
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_duplicate_goods_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            s1 = await product_factory.style(style_code="G006A")
            s2 = await product_factory.style(style_code="G006B")
            svc = GoodsService(session)
            await svc.create(
                GoodsMainCreate(
                    goods_code="DUP",
                    goods_title="先建的",
                    items=[GoodsStyleItemIn(style_id=s1.id)],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            with pytest.raises(GoodsCodeConflictError):
                await svc.create(
                    GoodsMainCreate(
                        goods_code="DUP",
                        goods_title="后建的",
                        items=[GoodsStyleItemIn(style_id=s2.id)],
                    ),
                    tenant_id=tenant_a.id,
                    user_id=user.id,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_unknown_style(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            deleted = await product_factory.style(style_code="G007", is_deleted=True)
            svc = GoodsService(session)
            with pytest.raises(ValidationError) as exc:
                await svc.create(
                    GoodsMainCreate(
                        goods_code="G007",
                        goods_title="挂了已删款式",
                        items=[GoodsStyleItemIn(style_id=deleted.id)],
                    ),
                    tenant_id=tenant_a.id,
                    user_id=user.id,
                )
            assert exc.value.code == "INVALID_STYLE_REFERENCE"
        finally:
            tenant_id_ctx.reset(token)


class TestUpdateGoods:
    async def test_items_replaced_and_is_suit_recomputed(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """套装拆回单品：成员从 2 个减到 1 个，is_suit 必须跟着变 false。

        这是 is_suit 不接受前端传值的原因 —— 让它由成员数推导，两个字段永远一致。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            a = await product_factory.style(style_code="G010A")
            b = await product_factory.style(style_code="G010B")
            svc = GoodsService(session)
            created = await svc.create(
                GoodsMainCreate(
                    goods_code="G010",
                    goods_title="两件套",
                    items=[
                        GoodsStyleItemIn(style_id=a.id, single_goods_cost=Decimal("10.00")),
                        GoodsStyleItemIn(style_id=b.id, single_goods_cost=Decimal("20.00")),
                    ],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            assert created.is_suit is True

            updated = await svc.update(
                created.id,
                GoodsMainUpdate(
                    goods_title="拆成单品",
                    items=[GoodsStyleItemIn(style_id=a.id, single_goods_cost=Decimal("10.00"))],
                ),
                user_id=user.id,
            )
            assert updated.is_suit is False
            assert len(updated.items) == 1
            assert updated.items[0].style_id == a.id
            assert updated.total_cost == Decimal("10.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_without_items_keeps_members(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """只改标题不传 items 时成员不动 —— 避免一次改名把套装成员清空。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            a = await product_factory.style(style_code="G011A")
            b = await product_factory.style(style_code="G011B")
            svc = GoodsService(session)
            created = await svc.create(
                GoodsMainCreate(
                    goods_code="G011",
                    goods_title="原名",
                    items=[
                        GoodsStyleItemIn(style_id=a.id),
                        GoodsStyleItemIn(style_id=b.id),
                    ],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            updated = await svc.update(
                created.id, GoodsMainUpdate(goods_title="新名"), user_id=user.id
            )
            assert updated.goods_title == "新名"
            assert updated.is_suit is True
            assert len(updated.items) == 2
        finally:
            tenant_id_ctx.reset(token)


class TestDeleteGoods:
    async def test_reject_delete_with_platform_links(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """挂着链接的商品删不掉 —— 否则千牛那条链接的销售数据就没有归属对象了。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="G020")
            svc = GoodsService(session)
            created = await svc.create(
                GoodsMainCreate(
                    goods_code="G020",
                    goods_title="已上架商品",
                    items=[GoodsStyleItemIn(style_id=style.id)],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            session.add(
                PlatformProduct(
                    tenant_id=tenant_a.id,
                    platform="千牛",
                    platform_id="1000000001",
                    style_id=style.id,
                    goods_main_id=created.id,
                    channel="普通",
                )
            )
            await session.flush()

            with pytest.raises(GoodsHasLinksError) as exc:
                await svc.soft_delete(created.id, user_id=user.id)
            assert exc.value.details["platform_links"] == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_soft_delete_leaves_trace_and_blocks_code_reuse(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """软删留痕；编码不可复用，否则历史报表会指向一个语义不同的新商品。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            s1 = await product_factory.style(style_code="G021A")
            s2 = await product_factory.style(style_code="G021B")
            svc = GoodsService(session)
            created = await svc.create(
                GoodsMainCreate(
                    goods_code="G021",
                    goods_title="要删的商品",
                    items=[GoodsStyleItemIn(style_id=s1.id)],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            await svc.soft_delete(created.id, user_id=user.id)

            repo = GoodsRepository(session)
            assert await repo.get_by_id(created.id) is None
            gone = await repo.get_by_id(created.id, include_deleted=True)
            assert gone is not None
            assert gone.is_deleted is True
            assert gone.is_active is False
            assert gone.deleted_by == user.id
            assert gone.deleted_at is not None

            with pytest.raises(GoodsCodeConflictError):
                await svc.create(
                    GoodsMainCreate(
                        goods_code="G021",
                        goods_title="想复用编码",
                        items=[GoodsStyleItemIn(style_id=s2.id)],
                    ),
                    tenant_id=tenant_a.id,
                    user_id=user.id,
                )
        finally:
            tenant_id_ctx.reset(token)


class TestListGoods:
    async def test_keyword_matches_member_style_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """用款号搜得到套装 —— 运维手里常常只有货号，不知道套装叫什么。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            inner = await product_factory.style(style_code="NEEDLE42", style_name="里面那件")
            other = await product_factory.style(style_code="G030B")
            svc = GoodsService(session)
            await svc.create(
                GoodsMainCreate(
                    goods_code="SUIT-G30",
                    goods_title="完全不含关键词的套装名",
                    items=[
                        GoodsStyleItemIn(style_id=inner.id),
                        GoodsStyleItemIn(style_id=other.id),
                    ],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            items, total = await svc.list_goods(
                tenant_id=tenant_a.id, filters=GoodsListFilters(keyword="NEEDLE42")
            )
            assert total == 1
            assert items[0].goods_code == "SUIT-G30"
            assert items[0].is_suit is True
        finally:
            tenant_id_ctx.reset(token)

    async def test_filter_is_suit_and_unlinked_only(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            a = await product_factory.style(style_code="G031A")
            b = await product_factory.style(style_code="G031B")
            c = await product_factory.style(style_code="G031C")
            svc = GoodsService(session)
            single = await svc.create(
                GoodsMainCreate(
                    goods_code="G031-SINGLE",
                    goods_title="单品",
                    items=[GoodsStyleItemIn(style_id=a.id)],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            await svc.create(
                GoodsMainCreate(
                    goods_code="G031-SUIT",
                    goods_title="套装",
                    items=[
                        GoodsStyleItemIn(style_id=b.id),
                        GoodsStyleItemIn(style_id=c.id),
                    ],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            # 只给单品挂链接，套装留空
            session.add(
                PlatformProduct(
                    tenant_id=tenant_a.id,
                    platform="千牛",
                    platform_id="1000000031",
                    style_id=a.id,
                    goods_main_id=single.id,
                    channel="普通",
                )
            )
            await session.flush()

            suits, suit_total = await svc.list_goods(
                tenant_id=tenant_a.id, filters=GoodsListFilters(is_suit=True)
            )
            assert suit_total == 1
            assert suits[0].goods_code == "G031-SUIT"

            unlinked, unlinked_total = await svc.list_goods(
                tenant_id=tenant_a.id, filters=GoodsListFilters(unlinked_only=True)
            )
            assert unlinked_total == 1
            assert unlinked[0].goods_code == "G031-SUIT"
            assert unlinked[0].link_count == 0

            linked, _ = await svc.list_goods(
                tenant_id=tenant_a.id, filters=GoodsListFilters(keyword="G031-SINGLE")
            )
            assert linked[0].link_count == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_cost_not_multiplied_by_link_count(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """一个商品挂两条链接（普通 + 直播）时，总成本不能被链接数翻倍。

        列表里成本与链接数是两个不同表的聚合，用 JOIN + GROUP BY 会让成员行与链接行
        相乘，所以仓储层用的是标量子查询。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="G040")
            svc = GoodsService(session)
            created = await svc.create(
                GoodsMainCreate(
                    goods_code="G040",
                    goods_title="两个渠道都在卖",
                    items=[GoodsStyleItemIn(style_id=style.id, single_goods_cost=Decimal("50.00"))],
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            for pid, channel in (("1000000040", "普通"), ("1000000041", "直播")):
                session.add(
                    PlatformProduct(
                        tenant_id=tenant_a.id,
                        platform="千牛",
                        platform_id=pid,
                        style_id=style.id,
                        goods_main_id=created.id,
                        channel=channel,
                    )
                )
            await session.flush()

            items, _ = await svc.list_goods(
                tenant_id=tenant_a.id, filters=GoodsListFilters(keyword="G040")
            )
            assert items[0].link_count == 2
            assert items[0].total_cost == Decimal("50.00")
        finally:
            tenant_id_ctx.reset(token)
