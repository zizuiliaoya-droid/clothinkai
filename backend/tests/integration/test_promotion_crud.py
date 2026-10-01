"""U04 Promotion CRUD 集成测试（EP05-S02 / S03 / S04 / S05）。

覆盖：
- 创建推广 + 自动 internal_code 生成
- 字段快照（style_code / style_short_name / quote_amount）
- 重复检测（EP05-S04 warning，非阻塞）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.enums import CooperationMode
from app.modules.promotion.exceptions import (
    CooperationModeImmutableError,
    InvalidBloggerReferenceError,
    InvalidGoodsReferenceError,
    InvalidStyleReferenceError,
    PromotionNotFoundError,
)
from app.modules.promotion.schemas import (
    PromotionCreate,
    PromotionListFilters,
    PromotionUpdate,
)
from app.modules.promotion.service import PromotionService


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreatePromotion:
    async def test_create_basic_with_blogger_quote_snapshot(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """EP05-S02: 创建推广，blogger.quote 自动快照为 quote_amount."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(short_name="测试简称")
            blogger = await blogger_factory.blogger(quote=Decimal("888.00"))
            svc = PromotionService(session)
            response = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            assert response.style_code_snapshot == style.style_code
            assert response.style_short_name_snapshot == "测试简称"
            assert response.quote_amount == Decimal("888.00")
            assert response.publish_status == "未发布"
            assert response.recall_status == "未召回"
            assert response.settlement_status == "未核查"
            assert response.is_active is True
            # internal_code 格式：<前缀><yyMMdd><0001>
            assert response.internal_code.endswith("0001")
            assert "260526" in response.internal_code
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_short_name_fallback(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """EP05-S03: short_name 为 None 时 fallback 用 style_name."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_name="完整款名", short_name=None)
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)
            response = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            assert response.style_short_name_snapshot == "完整款名"
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_explicit_quote_amount_override(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """PR 显式传 quote_amount 优先于 blogger.quote."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            svc = PromotionService(session)
            response = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                    quote_amount=Decimal("999.00"),
                ),
                user,
            )
            assert response.quote_amount == Decimal("999.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_nonexistent_style(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
    ) -> None:
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)
            with pytest.raises(InvalidStyleReferenceError):
                await svc.create_promotion(
                    PromotionCreate(
                        cooperation_mode=CooperationMode.GIFT,
                        style_id=uuid4(),
                        blogger_id=blogger.id,
                        platform="小红书",
                        cooperation_date=date(2026, 5, 26),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_nonexistent_blogger(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            svc = PromotionService(session)
            with pytest.raises(InvalidBloggerReferenceError):
                await svc.create_promotion(
                    PromotionCreate(
                        cooperation_mode=CooperationMode.GIFT,
                        style_id=style.id,
                        blogger_id=uuid4(),
                        platform="小红书",
                        cooperation_date=date(2026, 5, 26),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestGoodsAttribution:
    """商品归属：决定这笔推广费算给哪个商品的投产比。"""

    @staticmethod
    async def _goods(
        session: AsyncSession, tenant: Any, *styles: Any, code: str, is_suit: bool = False
    ) -> Any:
        from app.modules.product.goods_models import GoodsMain, GoodsStyleItem

        goods = GoodsMain(
            tenant_id=tenant.id,
            goods_code=code,
            goods_title=code,
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

    async def test_auto_resolves_main_goods_when_not_given(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """不传归属时取主商品（非套装优先），保证旧客户端与 Excel 导入行为不变。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="GA_AUTO")
            partner = await product_factory.style(style_code="GA_PARTNER")
            solo = await self._goods(session, tenant_a, style, code="GA-SOLO")
            await self._goods(session, tenant_a, style, partner, code="GA-SUIT", is_suit=True)
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            resp = await PromotionService(session).create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 6, 20),
                ),
                user,
            )
            assert resp.goods_main_id == solo.id
            assert resp.goods_code == "GA-SOLO"
            assert resp.goods_is_suit is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_explicit_goods_is_kept(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="GA_EXP")
            partner = await product_factory.style(style_code="GA_EXP_P")
            await self._goods(session, tenant_a, style, code="GA-EXP-SOLO")
            suit = await self._goods(
                session, tenant_a, style, partner, code="GA-EXP-SUIT", is_suit=True
            )
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            resp = await PromotionService(session).create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    goods_main_id=suit.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 6, 20),
                ),
                user,
            )
            assert resp.goods_main_id == suit.id
            assert resp.goods_is_suit is True
        finally:
            tenant_id_ctx.reset(token)

    async def test_goods_not_containing_style_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """不能把推广挂到不含该款式的商品上。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="GA_BAD")
            other = await product_factory.style(style_code="GA_OTHER")
            await self._goods(session, tenant_a, style, code="GA-BAD-OWN")
            unrelated = await self._goods(session, tenant_a, other, code="GA-UNRELATED")
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            with pytest.raises(InvalidGoodsReferenceError):
                await PromotionService(session).create_promotion(
                    PromotionCreate(
                        cooperation_mode=CooperationMode.GIFT,
                        style_id=style.id,
                        goods_main_id=unrelated.id,
                        blogger_id=blogger.id,
                        platform="小红书",
                        cooperation_date=date(2026, 6, 20),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_goods_attribution(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """归属可改 —— 录错或套装后建都要能修，改完投产报表跟着变。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="GA_UPD")
            partner = await product_factory.style(style_code="GA_UPD_P")
            solo = await self._goods(session, tenant_a, style, code="GA-UPD-SOLO")
            suit = await self._goods(
                session, tenant_a, style, partner, code="GA-UPD-SUIT", is_suit=True
            )
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            svc = PromotionService(session)
            created = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 6, 20),
                ),
                user,
            )
            assert created.goods_main_id == solo.id

            updated = await svc.update_promotion(
                created.id, PromotionUpdate(goods_main_id=suit.id), user
            )
            assert updated.goods_main_id == suit.id
            assert updated.goods_code == "GA-UPD-SUIT"
            assert updated.goods_is_suit is True

            with pytest.raises(InvalidGoodsReferenceError):
                other = await product_factory.style(style_code="GA_UPD_X")
                unrelated = await self._goods(session, tenant_a, other, code="GA-UPD-X")
                await svc.update_promotion(
                    created.id, PromotionUpdate(goods_main_id=unrelated.id), user
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestDuplicateWarning:
    async def test_duplicate_returns_warning_not_block(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """EP05-S04: 同款 + 同博主再创建返回 warnings 而非阻塞."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()

            # 已存在 1 条活跃推广
            existing = await promotion_factory.promotion(style=style, blogger=blogger, pr=user)

            svc = PromotionService(session)
            response = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 27),
                ),
                user,
            )
            # 不阻塞：返回新的 promotion + duplicate_warnings 含已存在的
            assert response.id != existing.id
            assert len(response.duplicate_warnings) == 1
            assert response.duplicate_warnings[0].promotion_id == existing.id
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSequenceGeneration:
    async def test_sequence_increments_per_day(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """同租户同 cooperation_date 序号递增 0001 → 0002."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)

            r1 = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            r2 = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="抖音",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            assert r1.internal_code.endswith("0001")
            assert r2.internal_code.endswith("0002")
        finally:
            tenant_id_ctx.reset(token)

    async def test_sequence_resets_per_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            svc = PromotionService(session)

            r1 = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 26),
                ),
                user,
            )
            r2 = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 5, 27),
                ),
                user,
            )
            # 不同日期独立计数
            assert r1.internal_code.endswith("0001")
            assert r2.internal_code.endswith("0001")

        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdatePromotion:
    async def test_update_quote_amount(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=user,
                quote_amount=Decimal("100.00"),
            )
            svc = PromotionService(session)
            response = await svc.update_promotion(
                promotion.id,
                PromotionUpdate(quote_amount=Decimal("300.00")),
                user,
            )
            assert response.quote_amount == Decimal("300.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_nonexistent(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = PromotionService(session)
            with pytest.raises(PromotionNotFoundError):
                await svc.update_promotion(
                    uuid4(),
                    PromotionUpdate(remark="x"),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestWarehouseFilters:
    """仓库打单的服务端筛选（source_extra 打单地址 / 发货单号）。

    回归用户反馈「仓库打单页每次加载都很慢」：原实现拉一页 100 条推广后在浏览器里
    过滤打单地址，既慢又会漏掉第 101 条以后的打单单。筛选必须落在服务端。
    """

    async def _seed(
        self,
        *,
        factory: Any,
        tenant_a: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> Any:
        user = await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        # 1) 有地址、无单号 → 待打单
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=user,
            internal_code="WH_PENDING",
            source_extra={"打单地址": "浙江省杭州市某路 1 号"},
        )
        # 2) 有地址、有单号 → 已打单
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=user,
            internal_code="WH_DONE",
            source_extra={"打单地址": "广东省广州市某路 2 号", "发货单号": "SF123456"},
        )
        # 3) 无地址 → 不该出现在仓库打单页
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=user,
            internal_code="WH_NO_ADDR",
            source_extra={},
        )
        # 4) 地址只有空白 → 等同于没填
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=user,
            internal_code="WH_BLANK_ADDR",
            source_extra={"打单地址": "   "},
        )
        return user

    async def test_has_print_address_excludes_blank_and_missing(
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
            user = await self._seed(
                factory=factory,
                tenant_a=tenant_a,
                admin_role=admin_role,
                product_factory=product_factory,
                blogger_factory=blogger_factory,
                promotion_factory=promotion_factory,
            )
            svc = PromotionService(session)
            page = await svc.list_promotions(
                filters=PromotionListFilters(has_print_address=True),
                page=1,
                page_size=50,
                user=user,
            )
            codes = {p.internal_code for p in page.items}
            assert codes == {"WH_PENDING", "WH_DONE"}
            assert page.total == 2
        finally:
            tenant_id_ctx.reset(token)

    async def test_pending_and_done_buckets(
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
            user = await self._seed(
                factory=factory,
                tenant_a=tenant_a,
                admin_role=admin_role,
                product_factory=product_factory,
                blogger_factory=blogger_factory,
                promotion_factory=promotion_factory,
            )
            svc = PromotionService(session)

            pending = await svc.list_promotions(
                filters=PromotionListFilters(has_print_address=True, has_waybill=False),
                page=1,
                page_size=50,
                user=user,
            )
            assert {p.internal_code for p in pending.items} == {"WH_PENDING"}

            done = await svc.list_promotions(
                filters=PromotionListFilters(has_print_address=True, has_waybill=True),
                page=1,
                page_size=50,
                user=user,
            )
            assert {p.internal_code for p in done.items} == {"WH_DONE"}
        finally:
            tenant_id_ctx.reset(token)

    async def test_filters_absent_returns_everything(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """不传这两个筛选时行为不变（其它页面不受影响）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await self._seed(
                factory=factory,
                tenant_a=tenant_a,
                admin_role=admin_role,
                product_factory=product_factory,
                blogger_factory=blogger_factory,
                promotion_factory=promotion_factory,
            )
            svc = PromotionService(session)
            page = await svc.list_promotions(
                filters=PromotionListFilters(),
                page=1,
                page_size=50,
                user=user,
            )
            assert page.total == 4
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestCooperationMode:
    """合作模式的成本口径与不可变约束（PRD V1.4 模块二）。

    PRD 反复强调这几条「后端必须强制，不可只靠前端」，所以这里全部用「前端传了错的值」
    的方式测 —— 传进去的数字必须被后端改掉，而不是被接受。
    """

    @staticmethod
    async def _goods_with_cost(
        session: AsyncSession,
        tenant: Any,
        *pairs: tuple[Any, Decimal | None],
        code: str,
        is_suit: bool = False,
    ) -> Any:
        """建一个商品，成员款式带单件货品成本。"""
        from app.modules.product.goods_models import GoodsMain, GoodsStyleItem

        goods = GoodsMain(
            tenant_id=tenant.id,
            goods_code=code,
            goods_title=code,
            is_suit=is_suit,
        )
        session.add(goods)
        await session.flush()
        for idx, (style, cost) in enumerate(pairs):
            session.add(
                GoodsStyleItem(
                    tenant_id=tenant.id,
                    goods_main_id=goods.id,
                    style_id=style.id,
                    single_goods_cost=cost,
                    sort_order=idx,
                )
            )
        await session.flush()
        return goods

    async def test_consignment_forces_sample_cost_to_zero(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """寄拍：衣服要寄回，样品成本恒为 0，即使商品成员填了成本。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="CM_CONSIGN")
            await self._goods_with_cost(
                session, tenant_a, (style, Decimal("200.00")), code="CM-CONSIGN"
            )
            blogger = await blogger_factory.blogger(quote=Decimal("500.00"))
            resp = await PromotionService(session).create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.CONSIGNMENT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 7, 1),
                ),
                user,
            )
            assert resp.cooperation_mode == "寄拍"
            assert resp.cost_snapshot == Decimal("0.00")
            assert resp.quote_amount == Decimal("500.00")
            # 站外推广成本 = 服务费 + 0 + 无运费
            assert resp.total_promo_cost == Decimal("500.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_gift_takes_sum_of_member_costs(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """送拍：样品成本 = 商品启用成员的单件货品成本之和（套装就是整套的钱）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            top = await product_factory.style(style_code="CM_GIFT_TOP")
            skirt = await product_factory.style(style_code="CM_GIFT_SKIRT")
            await self._goods_with_cost(
                session,
                tenant_a,
                (top, Decimal("72.50")),
                (skirt, Decimal("38.00")),
                code="CM-GIFT-SUIT",
                is_suit=True,
            )
            blogger = await blogger_factory.blogger(quote=Decimal("300.00"))
            resp = await PromotionService(session).create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=top.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 7, 2),
                ),
                user,
            )
            assert resp.cost_snapshot == Decimal("110.50")
            assert resp.quote_amount == Decimal("300.00")
            assert resp.total_promo_cost == Decimal("410.50")
        finally:
            tenant_id_ctx.reset(token)

    async def test_barter_forces_quote_to_zero(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """置换：以货换推广，博主服务费恒为 0，但样品成本照算。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="CM_BARTER")
            await self._goods_with_cost(
                session, tenant_a, (style, Decimal("88.00")), code="CM-BARTER"
            )
            blogger = await blogger_factory.blogger(quote=Decimal("600.00"))
            resp = await PromotionService(session).create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.BARTER,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 7, 3),
                    quote_amount=Decimal("600.00"),  # 前端硬塞一个报价，必须被压回 0
                ),
                user,
            )
            assert resp.quote_amount == Decimal("0.00")
            assert resp.cost_snapshot == Decimal("88.00")
            assert resp.total_promo_cost == Decimal("88.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_return_shipping_fee_counts_into_total(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """寄回运费计入站外推广成本；寄拍场景下它是唯一的非服务费成本。

        total_promo_cost 是数据库生成列，这里顺带验证它会跟着 PATCH 自动重算。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="CM_FEE")
            await self._goods_with_cost(
                session, tenant_a, (style, Decimal("150.00")), code="CM-FEE"
            )
            blogger = await blogger_factory.blogger(quote=Decimal("400.00"))
            svc = PromotionService(session)
            created = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.CONSIGNMENT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 7, 4),
                ),
                user,
            )
            assert created.total_promo_cost == Decimal("400.00")

            updated = await svc.update_promotion(
                created.id,
                PromotionUpdate(return_shipping_fee=Decimal("12.50")),
                user,
            )
            assert updated.return_shipping_fee == Decimal("12.50")
            # 400 服务费 + 0 样品成本（寄拍）+ 12.50 运费
            assert updated.total_promo_cost == Decimal("412.50")
        finally:
            tenant_id_ctx.reset(token)

    async def test_mode_cannot_be_changed_once_set(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """单据生成后合作模式锁死 —— 改它等于改成本口径，历史报表会对不上。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="CM_LOCK")
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            svc = PromotionService(session)
            created = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 7, 5),
                ),
                user,
            )
            with pytest.raises(CooperationModeImmutableError):
                await svc.update_promotion(
                    created.id,
                    PromotionUpdate(cooperation_mode=CooperationMode.BARTER),
                    user,
                )
            # 传同一个值不算修改，不该报错
            same = await svc.update_promotion(
                created.id,
                PromotionUpdate(cooperation_mode=CooperationMode.GIFT),
                user,
            )
            assert same.cooperation_mode == "送拍"
        finally:
            tenant_id_ctx.reset(token)

    async def test_legacy_null_mode_can_be_filled_once(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """历史导入数据没有合作模式，允许补一次，补完即锁。

        生产上有 5154 条这样的记录（Excel 导入的「未发布」老单）。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="CM_LEGACY")
            blogger = await blogger_factory.blogger(quote=Decimal("250.00"))
            svc = PromotionService(session)
            created = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 7, 6),
                ),
                user,
            )
            # 人为还原成历史数据的样子
            promotion = await svc._repo.get_by_id(created.id)
            assert promotion is not None
            promotion.cooperation_mode = None
            await session.flush()

            filled = await svc.update_promotion(
                created.id,
                PromotionUpdate(cooperation_mode=CooperationMode.BARTER),
                user,
            )
            assert filled.cooperation_mode == "置换"
            # 补成置换，服务费被归零
            assert filled.quote_amount == Decimal("0.00")

            with pytest.raises(CooperationModeImmutableError):
                await svc.update_promotion(
                    created.id,
                    PromotionUpdate(cooperation_mode=CooperationMode.GIFT),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_patching_quote_on_barter_is_forced_back_to_zero(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """置换单事后 PATCH 一个报价，仍然被压回 0 —— 强制规则不只在建单时生效。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="CM_PATCH")
            blogger = await blogger_factory.blogger(quote=Decimal("100.00"))
            svc = PromotionService(session)
            created = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.BARTER,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 7, 7),
                ),
                user,
            )
            assert created.quote_amount == Decimal("0.00")

            updated = await svc.update_promotion(
                created.id,
                PromotionUpdate(quote_amount=Decimal("999.00")),
                user,
            )
            assert updated.quote_amount == Decimal("0.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_gift_sample_cost_can_be_manually_adjusted(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """送拍的样品成本 PRD 允许 PR 手动微调，更新时不能被汇总值重新覆盖。

        这是 _enforce_mode_costs 与 _resolve_mode_costs 分开的原因：前者只压两个恒为 0
        的字段，不去重算可人工调整的部分。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="CM_ADJUST")
            await self._goods_with_cost(
                session, tenant_a, (style, Decimal("100.00")), code="CM-ADJUST"
            )
            blogger = await blogger_factory.blogger(quote=Decimal("200.00"))
            svc = PromotionService(session)
            created = await svc.create_promotion(
                PromotionCreate(
                    cooperation_mode=CooperationMode.GIFT,
                    style_id=style.id,
                    blogger_id=blogger.id,
                    platform="小红书",
                    cooperation_date=date(2026, 7, 8),
                ),
                user,
            )
            assert created.cost_snapshot == Decimal("100.00")

            # 尾货拼单，实际只按 60 核算
            adjusted = await svc.update_promotion(
                created.id,
                PromotionUpdate(remark="尾货，按 60 核算"),
                user,
            )
            assert adjusted.cost_snapshot == Decimal("100.00")

            promotion = await svc._repo.get_by_id(created.id)
            assert promotion is not None
            promotion.cost_snapshot = Decimal("60.00")
            await session.flush()

            # 再次更新别的字段，手调过的成本不该被汇总值冲掉
            again = await svc.update_promotion(
                created.id,
                PromotionUpdate(note_title="标题改一下"),
                user,
            )
            assert again.cost_snapshot == Decimal("60.00")
            assert again.total_promo_cost == Decimal("260.00")
        finally:
            tenant_id_ctx.reset(token)
