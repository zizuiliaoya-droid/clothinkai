"""推广单「品名」改用商品简称（7a-8），以及归属商品不再靠编码认（10-06 编码隐藏）。

规则只有 ``promotion/display_name.py`` 一处：商品简称去首尾空格后非空就用它，否则回落
建单快照 ``style_short_name_snapshot``。这里同一租户同时建四种单，逐条路径核对：

- A：商品有简称「飞狐短裙」（全称是另一串长名字），商品编码与款号不同
- B：商品简称是全空白（ORM 直接写，绕过接口归一）
- B2：商品简称是 NULL
- C：没有归属商品

路径：列表 SQL、详情 Python、仓库打单筛选、关键词搜索、企微催发渲染、催发任务列表 /
详情、博主 hover 历史合作。每个路径都断言 A 显示简称、其余回落快照，快照本身原样不动。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.negotiation.service import NegotiationService
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.promotion.schemas import PromotionListFilters, PromotionResponse
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from app.modules.urge.schemas import UrgeTaskListFilters
from app.modules.urge.service import UrgeService
from app.modules.wecom.models import WecomContact
from app.modules.wecom.scan_service import WecomScanService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_SHORT_A = "飞狐短裙"
# 全称里刻意不含简称的字：否则「按简称搜」靠全称也能命中，测不出简称那一列
_TITLE_A = "2026秋冬法式复古高腰A字半身裙"
_CODE_A = "SUIT-TEST-7A8"
_TITLE_B = "乙商品店铺标题很长很长"
_TITLE_B2 = "丙商品店铺标题很长很长"

# internal_code → 建单快照
_SNAPSHOT = {
    "DN-A-INT": "甲快照",
    "DN-B-INT": "乙快照",
    "DN-B2-INT": "丙快照",
    "DN-C-INT": "丁快照",
}

# internal_code → (display_short_name, goods_title, goods_short_name)
_EXPECTED: dict[str, tuple[str, str | None, str | None]] = {
    "DN-A-INT": (_SHORT_A, _TITLE_A, _SHORT_A),
    "DN-B-INT": ("乙快照", _TITLE_B, None),
    "DN-B2-INT": ("丙快照", _TITLE_B2, None),
    "DN-C-INT": ("丁快照", None, None),
}


@dataclass
class _Seed:
    user: Any
    promotions: dict[str, Any]  # internal_code → Promotion
    bloggers: dict[str, Any]  # internal_code → Blogger


async def _goods(
    session: AsyncSession,
    tenant: Any,
    style: Any,
    *,
    code: str,
    title: str,
    short_name: str | None,
    is_suit: bool = False,
) -> GoodsMain:
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=code,
        goods_title=title,
        short_name=short_name,
        is_suit=is_suit,
    )
    session.add(goods)
    await session.flush()
    session.add(GoodsStyleItem(tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id))
    await session.flush()
    return goods


async def _seed(
    session: AsyncSession,
    tenant_a: Any,
    factory: Any,
    admin_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    *,
    shared_blogger: bool = False,
) -> _Seed:
    user = await factory.user(tenant_a, roles=[admin_role])
    shared = await blogger_factory.blogger(nickname="同一个博主") if shared_blogger else None
    today = get_today()
    # internal_code → (款号, 商品参数或 None)
    specs: dict[str, tuple[str, dict[str, Any] | None]] = {
        "DN-A-INT": (
            "DN-A-ST",
            {"code": _CODE_A, "title": _TITLE_A, "short_name": _SHORT_A, "is_suit": True},
        ),
        # 全空白：ORM 直接写，绕过接口的去空白归一
        "DN-B-INT": ("DN-B-ST", {"code": "G-DN-B", "title": _TITLE_B, "short_name": "   "}),
        "DN-B2-INT": ("DN-B2-ST", {"code": "G-DN-B2", "title": _TITLE_B2, "short_name": None}),
        "DN-C-INT": ("DN-C-ST", None),
    }
    promotions: dict[str, Any] = {}
    bloggers: dict[str, Any] = {}
    for internal_code, (style_code, goods_kw) in specs.items():
        style = await product_factory.style(style_code=style_code, style_name=f"{style_code} 款名")
        goods = await _goods(session, tenant_a, style, **goods_kw) if goods_kw else None
        blogger = shared or await blogger_factory.blogger(nickname=f"博主{internal_code}")
        promotions[internal_code] = await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=user,
            internal_code=internal_code,
            style_short_name_snapshot=_SNAPSHOT[internal_code],
            goods_main_id=goods.id if goods else None,
            ship_status="待打单",
            receiver_address=f"{internal_code} 的收件地址",
            # A、C 排在 5 天后 → 进企微催发候选；B、B2 不排期，不进候选
            scheduled_publish_date=(
                today + timedelta(days=5) if internal_code in ("DN-A-INT", "DN-C-INT") else None
            ),
        )
        bloggers[internal_code] = blogger
    return _Seed(user=user, promotions=promotions, bloggers=bloggers)


def _names(item: Any) -> tuple[str | None, str | None, str | None]:
    return (item.display_short_name, item.goods_title, item.goods_short_name)


async def _list(session: AsyncSession, user: Any, **filters: Any) -> dict[str, PromotionResponse]:
    page = await PromotionService(session).list_promotions(
        filters=PromotionListFilters(**filters), page=1, page_size=50, user=user
    )
    return {p.internal_code: p for p in page.items}


async def _seed_contact(session: AsyncSession, tenant_id: UUID, blogger_id: UUID, ext: str) -> None:
    session.add(
        WecomContact(
            tenant_id=tenant_id,
            blogger_id=blogger_id,
            external_userid=ext,
            matched_wechat="wx",
            bound_at=datetime.now(UTC),
        )
    )
    await session.flush()


class TestPromotionDisplayShortName:
    async def test_list_uses_goods_short_name_else_snapshot(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """① 列表：A 显示简称；空白 / NULL / 没商品回落快照；快照与编码原样返回。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            seed = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            listed = await _list(session, seed.user)
            assert set(listed) == set(_EXPECTED)
            for code, expected in _EXPECTED.items():
                assert _names(listed[code]) == expected, code
                assert listed[code].style_short_name_snapshot == _SNAPSHOT[code], code
            # 编码接口照旧返回（导出 / 对账要用），只是界面不显示
            assert listed["DN-A-INT"].goods_code == _CODE_A
            assert listed["DN-A-INT"].goods_is_suit is True
            assert listed["DN-C-INT"].goods_code is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_detail_matches_list_exactly(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """② 详情（Python helper）与列表（SQL）三个字段逐字相等，含全空白简称那单。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            seed = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            listed = await _list(session, seed.user)
            svc = PromotionService(session)
            for code, promo in seed.promotions.items():
                detail = await svc.get_promotion(promo.id, seed.user)
                assert _names(detail) == _names(listed[code]), code
                assert _names(detail) == _EXPECTED[code], code
                assert detail.style_short_name_snapshot == _SNAPSHOT[code], code
        finally:
            tenant_id_ctx.reset(token)

    async def test_warehouse_filter_path_same_names(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """③ 带发货筛选（ship_status=待打单）走同一条列表 SQL，结果一样。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            seed = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            listed = await _list(session, seed.user, ship_status="待打单")
            assert set(listed) == set(_EXPECTED)
            for code, expected in _EXPECTED.items():
                assert _names(listed[code]) == expected, code
        finally:
            tenant_id_ctx.reset(token)

    async def test_keyword_hits_goods_short_name_title_and_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """④ 关键词：简称片段 / 全称片段 / 商品编码 都能搜到 A；内部编码照旧；无关词 0 条。

        编码在界面上隐藏了，但「隐藏显示 ≠ 不可查」（业务方 10-06）。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            seed = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            for keyword in ("飞狐", "法式复古", _CODE_A, "DN-A-INT"):
                hits = await _list(session, seed.user, keyword=keyword)
                assert set(hits) == {"DN-A-INT"}, keyword
            assert await _list(session, seed.user, keyword="八竿子打不着") == {}
        finally:
            tenant_id_ctx.reset(token)

    async def test_wecom_urge_template_uses_display_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """⑤ 企微催发的 {商品简称}：A 渲染简称，C（没商品）渲染快照。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            seed = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            blogger_a = seed.bloggers["DN-A-INT"]
            blogger_c = seed.bloggers["DN-C-INT"]
            await _seed_contact(session, tenant_a.id, blogger_a.id, "ext_dn_a")
            await _seed_contact(session, tenant_a.id, blogger_c.id, "ext_dn_c")

            created = await WecomScanService(session).scan_tenant(get_today())
            assert len(created) == 2

            rows = (
                await session.execute(
                    sa_text(
                        "SELECT blogger_id, rendered_content FROM wecom_message "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": created},
                )
            ).all()
            content = {r[0]: r[1] for r in rows}
            assert _SHORT_A in content[blogger_a.id]
            assert "甲快照" not in content[blogger_a.id]
            assert "丁快照" in content[blogger_c.id]
        finally:
            tenant_id_ctx.reset(token)

    async def test_urge_task_list_and_detail_use_display_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """⑥ 催发任务列表与详情：A 显示简称 + 全称，C 显示快照、没有全称。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            seed = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            svc = UrgeService(session)
            promo_a = seed.promotions["DN-A-INT"]
            promo_c = seed.promotions["DN-C-INT"]
            detail_a = await svc.urge_once(promo_a.id, seed.user)
            detail_c = await svc.urge_once(promo_c.id, seed.user)
            assert (detail_a.display_short_name, detail_a.goods_title) == (_SHORT_A, _TITLE_A)
            assert (detail_c.display_short_name, detail_c.goods_title) == ("丁快照", None)
            # 快照字段照旧
            assert detail_a.style_name == "甲快照"

            again = await svc.get_task_detail(detail_a.id, seed.user)
            assert again.display_short_name == _SHORT_A

            items, total = await svc.list_tasks(UrgeTaskListFilters(), seed.user)
            assert total == 2
            by_promo = {i.promotion_id: i for i in items}
            assert by_promo[promo_a.id].display_short_name == _SHORT_A
            assert by_promo[promo_a.id].goods_title == _TITLE_A
            assert by_promo[promo_c.id].display_short_name == "丁快照"
        finally:
            tenant_id_ctx.reset(token)

    async def test_blogger_history_uses_display_name(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """⑦ 博主 hover 卡的历史合作：同一博主的四单各按规则显示。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            seed = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
                shared_blogger=True,
            )
            blogger = seed.bloggers["DN-A-INT"]
            history = await NegotiationService(session).blogger_history(
                blogger.id, seed.user, limit=10
            )
            assert history.total_cooperations == 4
            by_code = {i.internal_code: i for i in history.items}
            for code, (display, title, _short) in _EXPECTED.items():
                assert (by_code[code].display_short_name, by_code[code].goods_title) == (
                    display,
                    title,
                ), code
                assert by_code[code].style_name == _SNAPSHOT[code], code
        finally:
            tenant_id_ctx.reset(token)
