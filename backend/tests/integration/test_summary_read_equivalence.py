"""汇总表读取路径与实时路径逐字相等 —— 方案 2 切读取的核心保证。

报表从实时聚合切到汇总表的那一刻，用户看到的数字**一分都不能变**。不等的话不会有
任何报错：页面照常渲染，只是数字悄悄不同了。所以这里每个报表都把两条路径的输出
序列化成 JSON 逐字比较（``model_dump(mode="json")``），不只比 Decimal 是否相等 ——
``Decimal("0") == Decimal("0.00")`` 为真，可页面上一个显示「¥0」一个显示「¥0.00」。

每条用例都先确认**汇总路径真的被走到了**（拦截 ``record_source``），否则两边都悄悄
回退实时，比较的就是同一条路径，永远相等。场景有效性也要断言：每个指标都要出现
非零值，不然可能是一堆 0 在和 0 比。

日期全部相对 ``get_today()`` 构造：工作进度的催发状态依赖今天，写死日期的话
CI 换一天跑结果就变了。场景放在「上个月」：整月都在过去，刷新能完整覆盖。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.report.production_service as production_mod
import app.modules.report.store_daily_service as store_mod
import app.modules.report.work_progress_service as work_mod
from app.core.config import settings
from app.core.security.field_permissions import FieldPermissionContext
from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import AdDaily, QianniuDaily
from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import PRODUCTION_COUNT_COLUMNS, ProductionRepository
from app.modules.report.advanced_schemas import ProductionRow, PrWorkProgress
from app.modules.report.pr_scope import PR_SCOPE_ALL, PrScope, PrScopeKind
from app.modules.report.production_service import ProductionService
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.summary_read import SummaryReadRepository
from app.modules.report.summary_refresh_service import SummaryRefreshService
from app.modules.report.work_progress_models import StoreDaily
from app.modules.report.work_progress_service import WorkProgressService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _prev_month(today: date) -> tuple[date, date]:
    last = today.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


TODAY = get_today()
M_LO, M_HI = _prev_month(TODAY)
MONTH = f"{M_LO:%Y-%m}"
D1, D2, D3 = M_LO, M_LO + timedelta(days=3), M_LO + timedelta(days=9)
# 上一期（投产报表的环比区间）里的一天
PREV_DAY = M_LO - timedelta(days=5)


class _SourceRecorder:
    """拦截 record_source，记下每次读取走了哪条路径。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, report: str, source: str) -> None:
        self.calls.append((report, source))


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _SourceRecorder:
    rec = _SourceRecorder()
    for mod in (production_mod, store_mod, work_mod):
        monkeypatch.setattr(mod, "record_source", rec)
    return rec


async def _goods(
    session: AsyncSession,
    tenant: Any,
    *styles: Any,
    code: str,
    season: str,
    is_suit: bool = False,
    short_name: str | None = None,
) -> GoodsMain:
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=code,
        goods_title=f"{code} 商品",
        short_name=short_name,
        is_suit=is_suit,
        season=season,
    )
    session.add(goods)
    await session.flush()
    for idx, style in enumerate(styles):
        session.add(
            GoodsStyleItem(
                tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id, sort_order=idx
            )
        )
    await session.flush()
    return goods


async def _pp(
    session: AsyncSession,
    tenant: Any,
    style: Any,
    goods: GoodsMain,
    *,
    platform: str = "千牛",
    channel: str = "普通",
    is_active: bool = True,
) -> PlatformProduct:
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform=platform,
        platform_id=f"P{uuid4().hex[:8]}",
        style_id=style.id,
        goods_main_id=goods.id,
        channel=channel,
        is_active=is_active,
    )
    session.add(pp)
    await session.flush()
    return pp


def _qn(
    tenant: Any,
    pp: PlatformProduct,
    day: date,
    pay: str,
    refund: str | None = None,
    add_cart: int | None = None,
    pay_orders: int = 5,
) -> QianniuDaily:
    return QianniuDaily(
        tenant_id=tenant.id,
        platform_product_id=pp.id,
        platform_id_snapshot=pp.platform_id,
        date=day,
        visitors=100,
        pay_amount=Decimal(pay),
        pay_orders=pay_orders,
        refund_amount=Decimal(refund) if refund is not None else None,
        add_cart_count=add_cart,
    )


async def _seed(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    factory: Any,
) -> dict[str, Any]:
    """两个商品（含套装）+ 只有刷单的商品 + 上一期数据 + 覆盖全部催发状态的推广单。"""
    style_a = await product_factory.style(style_code=f"EA{uuid4().hex[:6]}")
    # A 填了简称、B 没填：两条路径都得原样带出简称（含 NULL）
    goods_a = await _goods(
        session,
        tenant,
        style_a,
        code=f"G_A_{uuid4().hex[:5]}",
        season="春夏",
        short_name="A 简称",
    )
    qn_a = await _pp(session, tenant, style_a, goods_a)
    ad_a = await _pp(session, tenant, style_a, goods_a, platform="万相台")

    style_b1 = await product_factory.style(style_code=f"EB1{uuid4().hex[:5]}")
    style_b2 = await product_factory.style(style_code=f"EB2{uuid4().hex[:5]}")
    goods_b = await _goods(
        session,
        tenant,
        style_b1,
        style_b2,
        code=f"G_B_{uuid4().hex[:5]}",
        season="秋冬",
        is_suit=True,
    )
    qn_b = await _pp(session, tenant, style_b1, goods_b)

    style_c = await product_factory.style(style_code=f"EC{uuid4().hex[:6]}")
    await _goods(session, tenant, style_c, code=f"G_C_{uuid4().hex[:5]}", season="春夏")

    session.add_all(
        [
            _qn(tenant, qn_a, D1, "1000.00", refund="50.00", add_cart=7),
            # 导出里是 "-" / 缺列 → typed 列为 NULL，两条路径都按没有算、都不能炸
            _qn(tenant, qn_a, D3, "800.00"),
            _qn(tenant, qn_b, D2, "2000.00", refund="100.00", add_cart=3),
            # 上一期（环比）
            _qn(tenant, qn_a, PREV_DAY, "500.00", refund="5.00"),
            AdDaily(
                tenant_id=tenant.id,
                platform_product_id=ad_a.id,
                platform_id_snapshot=ad_a.platform_id,
                date=D1,
                cost=Decimal("120.00"),
                extra={},
            ),
            OrderAdjustment(
                tenant_id=tenant.id,
                order_type="刷单",
                style_id=style_a.id,
                amount=Decimal("200.00"),
                order_date=D2,
                exclude_from_roi=True,
                status="待付款",
            ),
            # 只有刷单的商品：两条路径都要被 HAVING 滤掉
            OrderAdjustment(
                tenant_id=tenant.id,
                order_type="刷单",
                style_id=style_c.id,
                amount=Decimal("333.00"),
                order_date=D1,
                exclude_from_roi=True,
                status="待付款",
            ),
            # 手填的店铺广告消耗：两条路径都读取时 LEFT JOIN
            StoreDaily(tenant_id=tenant.id, date=D1, ad_spend_total=Decimal("88.00")),
        ]
    )

    pr1 = await factory.user(tenant)
    pr2 = await factory.user(tenant)
    blogger = await blogger_factory.blogger()

    async def promo(style: Any, **kw: Any) -> None:
        # 工厂默认的 internal_code 只有 3 位随机十六进制（4096 种），这里一个场景建 13 张、
        # 全文件 24 个用例各建一遍，撞唯一索引的概率高到会随机失败。显式给足 10 位。
        kw.setdefault("internal_code", f"DE{uuid4().hex[:10].upper()}")
        await promotion_factory.promotion(style=style, blogger=blogger, **kw)

    # pr1：覆盖工作进度的每一个计数
    await promo(
        style_a,
        pr=pr1,
        goods_main_id=goods_a.id,
        cooperation_date=D2,
        publish_status="已发布",
        like_count=600,  # ≥ 500 → 爆文
        cost_snapshot=Decimal("30.00"),
        quote_amount=Decimal("400.00"),
    )
    await promo(
        style_b1,
        pr=pr1,
        goods_main_id=goods_b.id,
        cooperation_date=D3,
        publish_status="已发布",  # 没录点赞 → 不算信息完整
        quote_amount=Decimal("250.00"),
    )
    for offset in (20, 5, 1, -5):  # 档期内 / 催发 / 重要催发 / 超时
        await promo(
            style_a,
            pr=pr1,
            cooperation_date=D1,
            scheduled_publish_date=TODAY + timedelta(days=offset),
        )
    await promo(style_a, pr=pr1, cooperation_date=D1, publish_status="已取消")
    await promo(style_a, pr=pr1, cooperation_date=D2, recall_status="召回成功")
    await promo(style_a, pr=pr1, cooperation_date=D3, recall_status="召回中")
    # pr2：抖音点赞按 ×0.1 折算，两天各 1.5 —— 逐日存整数会变成 2+2=4，正确是 3
    for day in (D1, D2):
        await promo(
            style_b1,
            pr=pr2,
            cooperation_date=day,
            publish_status="已发布",
            platform="抖音",
            like_count=15,
        )
    # 未分配 PR 的单子也要进统计（pr_id 为 NULL，汇总表唯一索引靠 NULLS NOT DISTINCT）
    await promo(style_a, cooperation_date=D2)

    await session.flush()
    return {"goods_a": goods_a, "goods_b": goods_b, "pr1": pr1, "pr2": pr2}


async def _refresh(session: AsyncSession, tenant: Any, lo: date, hi: date) -> None:
    await SummaryRefreshService(session).refresh(tenant_id=tenant.id, date_from=lo, date_to=hi)
    await session.commit()


_SUPER_CTX = FieldPermissionContext(frozenset(), frozenset(), frozenset(), is_superuser=True)


async def _wp(
    svc: WorkProgressService,
    tenant: Any,
    lo: date,
    hi: date,
    *,
    scope: PrScope = PR_SCOPE_ALL,
) -> list[PrWorkProgress]:
    return await svc.get_for_range(tenant.id, lo, hi, scope=scope, field_ctx=_SUPER_CTX)


def _dump(models: Any) -> Any:
    if isinstance(models, list):
        return [m.model_dump(mode="json") for m in models]
    return models.model_dump(mode="json")


class TestProductionReport:
    @pytest.mark.parametrize("exclude_brushing", [True, False])
    # 类目筛选已下线（8a-3）：组合只剩「刷单开关 × 季节」
    @pytest.mark.parametrize(
        "seasons",
        [None, ["春夏"], ["秋冬"]],
        ids=["no_filter", "season_spring", "season_autumn"],
    )
    async def test_summary_equals_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
        exclude_brushing: bool,
        seasons: list[str] | None,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = ProductionService(session)
            kwargs = {
                "exclude_brushing": exclude_brushing,
                "seasons": seasons,
            }
            via_summary = await svc.get_report(tenant_a.id, (M_LO, M_HI), **kwargs)
            # 本期被覆盖 → 汇总表；上一期没刷 → 实时
            assert ("production", "summary") in recorder.calls
            assert ("production_previous", "live") in recorder.calls

            via_live = await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False, **kwargs)
            assert _dump(via_summary) == _dump(via_live)

            items = via_summary.items
            assert items, "场景没有产生投产行"
            if seasons is None:
                # 只有刷单的商品被 HAVING 滤掉，剩 A、B 两个
                assert len(items) == 2
                assert {r.goods_short_name for r in items} == {"A 简称", None}
                assert any(r.refund_amount != 0 for r in items)
                assert any(r.add_cart_count != 0 for r in items)
                assert any(r.promo_cost != 0 for r in items)
                assert any(r.ad_spend != 0 for r in items)
                assert via_summary.previous, "上一期没有数据，环比比较是空跑"
            else:
                # 季节筛选确实生效（不是两条路径都没筛、碰巧相等）：春夏只剩 A，秋冬只剩套装 B
                expected = seeded["goods_a"] if seasons == ["春夏"] else seeded["goods_b"]
                assert {str(r.goods_id) for r in items} == {str(expected.id)}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_brushing_toggle_changes_pay_amount(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """汇总表存含刷单原值，剔刷单在读取时减 —— 两个开关读出的支付额必须差刷单额。

        这条防的是「汇总路径忘了减刷单」：上面的等值用例在 exclude_brushing=True
        时也能发现，但这里直接钉死数字，出错时一眼看出差在哪。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = ProductionService(session)
            excluded = await svc.get_report(tenant_a.id, (M_LO, M_HI), exclude_brushing=True)
            included = await svc.get_report(tenant_a.id, (M_LO, M_HI), exclude_brushing=False)
            assert recorder.calls.count(("production", "summary")) == 2

            gid = seeded["goods_a"].id
            pay_ex = next(r.pay_amount for r in excluded.items if r.goods_id == gid)
            pay_in = next(r.pay_amount for r in included.items if r.goods_id == gid)
            assert pay_in == Decimal("1800.00")
            assert pay_ex == Decimal("1600.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_previous_period_from_summary_when_covered(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """上一期也被刷过时，两期都读汇总表，结果仍与实时一致。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await session.commit()
            span = M_HI - M_LO
            await _refresh(session, tenant_a, M_LO - span - timedelta(days=1), M_HI)

            svc = ProductionService(session)
            via_summary = await svc.get_report(tenant_a.id, (M_LO, M_HI))
            assert ("production_previous", "summary") in recorder.calls
            via_live = await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False)
            assert _dump(via_summary) == _dump(via_live)
            assert via_summary.previous
        finally:
            tenant_id_ctx.reset(tok)


async def _seed_channels(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> dict[str, Any]:
    """同一商品挂普通 + 直播两条千牛链接（7e R2：汇总表按渠道存，投产接口仍按商品一行）。

    商品 A（春夏）：
    - 普通千牛 D1 支付 1000 / 退款 50 / 加购 7，D2 支付 600 / 退款 20 / 加购 4；
    - 直播千牛整段**只有退款**（D3 退款 25、加购 2）—— 逐行 HAVING 会丢掉这一行；
    - 一条停用的千牛链接 D1 支付 300（7e-9：不进单品）；
    - 普通万相台只有 D2（花费 80、站内加购 9）→ 配对加购只配上 D2；
    - D2 已发布推广一单、刷单 200；上一期普通 200 + 直播 100。
    商品 B（秋冬）：普通千牛 D2 支付 2000，排在 A 前面。
    """
    style_a = await product_factory.style(style_code=f"CA{uuid4().hex[:6]}")
    goods_a = await _goods(session, tenant, style_a, code=f"G_CA_{uuid4().hex[:5]}", season="春夏")
    qn_norm = await _pp(session, tenant, style_a, goods_a)
    qn_live = await _pp(session, tenant, style_a, goods_a, channel="直播")
    qn_off = await _pp(session, tenant, style_a, goods_a, is_active=False)
    ad_norm = await _pp(session, tenant, style_a, goods_a, platform="万相台")

    style_b = await product_factory.style(style_code=f"CB{uuid4().hex[:6]}")
    goods_b = await _goods(session, tenant, style_b, code=f"G_CB_{uuid4().hex[:5]}", season="秋冬")
    qn_b = await _pp(session, tenant, style_b, goods_b)

    session.add_all(
        [
            _qn(tenant, qn_norm, D1, "1000.00", refund="50.00", add_cart=7),
            _qn(tenant, qn_norm, D2, "600.00", refund="20.00", add_cart=4, pay_orders=3),
            _qn(tenant, qn_live, D3, "0.00", refund="25.00", add_cart=2, pay_orders=0),
            _qn(tenant, qn_off, D1, "300.00", refund="10.00", add_cart=3),
            _qn(tenant, qn_norm, PREV_DAY, "200.00"),
            _qn(tenant, qn_live, PREV_DAY, "100.00"),
            _qn(tenant, qn_b, D2, "2000.00", refund="100.00", add_cart=5),
            AdDaily(
                tenant_id=tenant.id,
                platform_product_id=ad_norm.id,
                platform_id_snapshot=ad_norm.platform_id,
                date=D2,
                cost=Decimal("80.00"),
                add_cart_count=9,
                extra={},
            ),
            OrderAdjustment(
                tenant_id=tenant.id,
                order_type="刷单",
                style_id=style_a.id,
                amount=Decimal("200.00"),
                order_date=D2,
                exclude_from_roi=True,
                status="待付款",
            ),
        ]
    )
    blogger = await blogger_factory.blogger()
    await promotion_factory.promotion(
        style=style_a,
        blogger=blogger,
        goods_main_id=goods_a.id,
        cooperation_date=D2,
        publish_status="已发布",
        quote_amount=Decimal("400.00"),
        internal_code=f"CH{uuid4().hex[:10].upper()}",
    )
    await session.flush()
    promo_cost = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(total_promo_cost), 0) FROM promotion "
                "WHERE goods_main_id = CAST(:g AS uuid) AND publish_status = '已发布'"
            ),
            {"g": str(goods_a.id)},
        )
    ).scalar_one()
    assert promo_cost > 0, "推广成本为 0，场景失效"
    return {
        "goods_a": goods_a,
        "goods_b": goods_b,
        "qn_live": qn_live,
        "qn_off": qn_off,
        "promo_cost": Decimal(promo_cost).quantize(Decimal("0.01")),
    }


# 投产行的冻结键集合：R2 按渠道存汇总，但响应结构不变（r2-M1）
_PRODUCTION_ROW_KEYS = {
    "goods_id",
    "goods_code",
    "goods_title",
    "goods_short_name",
    "is_suit",
    "style_codes",
    "main_image_url",
    "pay_amount",
    "refund_amount",
    "return_rate",
    "confirmed_amount",
    "promo_cost",
    "ad_spend",
    "total_spend",
    "add_cart_count",
    "add_cart_cost",
    "net_roi",
    "unit_deal_cost",
    "extra",
}


def _norm_repo_row(row: Any) -> dict[str, Any]:
    """仓储层行逐字比：金额统一两位小数转字符串，计数转 int，其余原样。"""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in PRODUCTION_COUNT_COLUMNS:
            out[key] = int(value)
        elif isinstance(value, Decimal):
            out[key] = str(value.quantize(Decimal("0.01")))
        else:
            out[key] = value
    return out


def _money_tuple(row: Any) -> tuple[Any, ...]:
    return (
        str(row.goods_id),
        row.pay_amount,
        row.refund_amount,
        row.add_cart_count,
        row.promo_cost,
        row.ad_spend,
    )


class TestProductionChannels:
    """r2-M1：汇总表按（商品, 渠道）存，投产接口两条路径都按商品把渠道求和回一行。"""

    @pytest.mark.parametrize("exclude_brushing", [True, False])
    async def test_service_one_row_per_goods_both_paths(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        recorder: _SourceRecorder,
        exclude_brushing: bool,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_channels(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()
            span = M_HI - M_LO
            await _refresh(session, tenant_a, M_LO - span - timedelta(days=1), M_HI)

            svc = ProductionService(session)
            via_summary = await svc.get_report(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=exclude_brushing
            )
            assert recorder.calls == [
                ("production", "summary"),
                ("production_previous", "summary"),
            ]
            via_live = await svc.get_report(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=exclude_brushing, use_summary=False
            )
            assert recorder.calls[2:] == [("production", "live"), ("production_previous", "live")]
            assert _dump(via_summary) == _dump(via_live)

            a_id, b_id = str(seeded["goods_a"].id), str(seeded["goods_b"].id)
            # 每个商品一行，顺序 = 旧结构（支付额降序）
            assert [str(r.goods_id) for r in via_summary.items] == [b_id, a_id]
            assert via_summary.previous is not None
            prev = [r for r in via_summary.previous if str(r.goods_id) == a_id]
            assert len(prev) == 1
            assert prev[0].pay_amount == Decimal("300.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_matches_old_structure_minus_inactive_link(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """数字 = 改动前的按商品口径（只扣停用链接）；渠道翻转不改数；启用停用链接差额 = 它的数据。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_channels(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            a, b = seeded["goods_a"], seeded["goods_b"]
            promo = seeded["promo_cost"]

            svc = ProductionService(session)
            via_summary = await svc.get_report(tenant_a.id, (M_LO, M_HI))
            assert recorder.calls[0] == ("production", "summary")
            assert set(ProductionRow.model_fields) == _PRODUCTION_ROW_KEYS
            assert all(set(r.model_dump()) == _PRODUCTION_ROW_KEYS for r in via_summary.items)
            expected = [
                (
                    str(b.id),
                    Decimal("2000.00"),
                    Decimal("100.00"),
                    5,
                    Decimal("0.00"),
                    Decimal("0.00"),
                ),
                # 1000 + 600 − 刷单 200；退款含直播只有退款的那一天 25
                (str(a.id), Decimal("1400.00"), Decimal("95.00"), 13, promo, Decimal("80.00")),
            ]
            assert [_money_tuple(r) for r in via_summary.items] == expected

            live_before = _dump(await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False))
            assert live_before == _dump(via_summary)

            # 直播链接改成普通：渠道合并后数字一分不变
            await session.execute(
                update(PlatformProduct)
                .where(PlatformProduct.id == seeded["qn_live"].id)
                .values(channel="普通")
            )
            await session.commit()
            flipped = _dump(await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False))
            assert flipped == live_before

            # 启用停用链接：A 多出它那一天的支付 300 / 退款 10 / 加购 3，其余不变
            await session.execute(
                update(PlatformProduct)
                .where(PlatformProduct.id == seeded["qn_off"].id)
                .values(is_active=True)
            )
            await session.commit()
            enabled = await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False)
            assert [_money_tuple(r) for r in enabled.items] == [
                expected[0],
                (str(a.id), Decimal("1700.00"), Decimal("105.00"), 16, promo, Decimal("80.00")),
            ]
        finally:
            tenant_id_ctx.reset(tok)

    @pytest.mark.parametrize("exclude_brushing", [True, False])
    @pytest.mark.parametrize("seasons", [None, ["春夏"]], ids=["no_filter", "season_spring"])
    async def test_repository_rows_by_channel_equal(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        exclude_brushing: bool,
        seasons: list[str] | None,
    ) -> None:
        """仓储层两路按（商品, 渠道）逐行相等，含直播行与 062 的 5 列。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_channels(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            kwargs = {
                "tenant_id": tenant_a.id,
                "date_from": M_LO,
                "date_to": M_HI,
                "exclude_brushing": exclude_brushing,
                "seasons": seasons,
            }
            live = await ProductionRepository(session).aggregate_by_goods(**kwargs)
            summary = await SummaryReadRepository(session).production_by_goods(**kwargs)
            assert [_norm_repo_row(r) for r in summary] == [_norm_repo_row(r) for r in live]

            a_id, b_id = seeded["goods_a"].id, seeded["goods_b"].id
            keys = [(r["goods_id"], r["channel"]) for r in live]
            want = [(a_id, "普通"), (a_id, "直播")]
            assert keys == (want if seasons else [(b_id, "普通"), *want])

            rows = {(r["goods_id"], r["channel"]): r for r in live}
            normal, streaming = rows[(a_id, "普通")], rows[(a_id, "直播")]
            # 直播整段只有退款：靠商品级 HAVING 留下
            assert Decimal(streaming["pay_amount"]) == 0
            assert Decimal(streaming["refund_amount"]) == Decimal("25.00")
            # 062 的 5 列：千牛 D1、D2 + 万相台只有 D2 → 只配上 D2
            assert int(normal["pay_orders"]) == 8
            assert int(normal["ad_add_cart_count"]) == 9
            assert int(normal["paired_days"]) == 1
            assert int(normal["paired_all_cart"]) == 4
            assert int(normal["paired_ad_cart"]) == 9
        finally:
            tenant_id_ctx.reset(tok)


class TestProductionTrend:
    @pytest.mark.parametrize("granularity", ["day", "week", "month", "year"])
    @pytest.mark.parametrize("exclude_brushing", [True, False])
    async def test_summary_equals_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
        granularity: str,
        exclude_brushing: bool,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = ProductionService(session)
            for key in ("goods_a", "goods_b"):
                gid = seeded[key].id
                kwargs = {"granularity": granularity, "exclude_brushing": exclude_brushing}
                via_summary = await svc.get_trend(tenant_a.id, gid, (M_LO, M_HI), **kwargs)
                via_live = await svc.get_trend(
                    tenant_a.id, gid, (M_LO, M_HI), use_summary=False, **kwargs
                )
                assert _dump(via_summary) == _dump(via_live), f"{key} {granularity}"
                assert via_summary.points, f"{key} 没有趋势点"
            assert ("production_trend", "summary") in recorder.calls
        finally:
            tenant_id_ctx.reset(tok)


class TestWorkProgress:
    async def test_summary_equals_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = WorkProgressService(session)
            via_summary = await _wp(svc, tenant_a, M_LO, M_HI)
            assert ("work_progress", "summary") in recorder.calls

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await _wp(svc, tenant_a, M_LO, M_HI)
            assert ("work_progress", "live") in recorder.calls
            assert _dump(via_summary) == _dump(via_live)

            # 场景有效性：14 个计数每一个都要在某一行出现非零值
            counters = (
                "quote_count",
                "in_schedule_count",
                "urge_count",
                "important_urge_count",
                "overdue_count",
                "publish_count",
                "info_complete_count",
                "cancel_count",
                "recall_due_count",
                "recall_success_count",
                "effective_quote_count",
                "hit_count",
                "like_count",
                "cost",
            )
            for c in counters:
                assert any(getattr(r, c) for r in via_summary), f"场景没有产生非零的 {c}"

            by_pr = {r.pr_id: r for r in via_summary}
            # 抖音折算：两天各 1.5，合计 3（逐日取整会得到 4）
            assert by_pr[seeded["pr2"].id].like_count == 3
            # 未分配 PR 的那一行在
            assert None in by_pr
        finally:
            tenant_id_ctx.reset(tok)

    @pytest.mark.parametrize("who", ["pr1", "unassigned"])
    async def test_scoped_summary_equals_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
        monkeypatch: pytest.MonkeyPatch,
        who: str,
    ) -> None:
        """只看本人（read_own → ``PrScope(PR, 本人)``）与「未分配」：SQL 之后按行过滤，两路逐字相等。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            if who == "pr1":
                scope = PrScope(PrScopeKind.PR, seeded["pr1"].id)
                expected_pr = seeded["pr1"].id
            else:
                scope = PrScope(PrScopeKind.UNASSIGNED)
                expected_pr = None

            svc = WorkProgressService(session)
            via_summary = await _wp(svc, tenant_a, M_LO, M_HI, scope=scope)
            assert recorder.calls == [("work_progress", "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await _wp(svc, tenant_a, M_LO, M_HI, scope=scope)
            assert recorder.calls[1:] == [("work_progress", "live")]
            assert _dump(via_summary) == _dump(via_live)

            # 场景有效性：全量里有 3 行（pr1 / pr2 / 未分配），范围内只剩那一行，计数非零
            everyone = await _wp(svc, tenant_a, M_LO, M_HI)
            assert len(everyone) == 3
            assert [r.pr_id for r in via_summary] == [expected_pr]
            assert via_summary[0].quote_count > 0
        finally:
            tenant_id_ctx.reset(tok)

    async def test_range_with_future_days_stays_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """区间含还没到的日子，刷新覆盖不到 → 走实时（通用的「每天有覆盖」规则，不是当月特例）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _refresh(session, tenant_a, TODAY - timedelta(days=30), TODAY)
            svc = WorkProgressService(session)
            await _wp(svc, tenant_a, TODAY - timedelta(days=5), TODAY + timedelta(days=1))
            assert recorder.calls == [("work_progress", "live")]
        finally:
            tenant_id_ctx.reset(tok)

    async def test_fully_covered_range_reads_summary(
        self,
        session: AsyncSession,
        tenant_a: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """区间每天都有覆盖就读汇总：截到今天的区间（``this_month`` 这种）也不再写死实时。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _refresh(session, tenant_a, TODAY - timedelta(days=30), TODAY)
            svc = WorkProgressService(session)
            await _wp(svc, tenant_a, TODAY - timedelta(days=10), TODAY)
            assert recorder.calls == [("work_progress", "summary")]
        finally:
            tenant_id_ctx.reset(tok)


class TestStoreDaily:
    async def test_summary_equals_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = StoreDailyService(session)
            via_summary = await svc.get_dashboard(tenant_a.id, (M_LO, M_HI))
            assert recorder.calls == [("store_daily", "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await svc.get_dashboard(tenant_a.id, (M_LO, M_HI))
            assert _dump(via_summary) == _dump(via_live)

            assert len(via_summary) == 3  # D1 / D2 / D3 三天有千牛日报
            manual = next(r for r in via_summary if r.date == D1)
            assert manual.ad_spend_total == Decimal("88.00"), "手填广告消耗没有 LEFT JOIN 进来"
        finally:
            tenant_id_ctx.reset(tok)


class TestFreshness:
    async def test_fully_covered_reads_summary_with_oldest_refresh_time(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        await _refresh(session, tenant_a, M_LO, M_HI)
        repo = SummaryReadRepository(session)
        fresh = await repo.freshness(tenant_a.id, M_LO, M_HI)
        assert fresh.source == "summary"
        oldest = (
            await session.execute(
                text("SELECT min(refreshed_at) FROM report_summary_coverage WHERE tenant_id = :t"),
                {"t": tenant_a.id},
            )
        ).scalar_one()
        assert fresh.data_as_of == oldest

    async def test_one_missing_day_falls_back_to_live(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        """差一天也不行 —— 少的那天会被当成 0 算进区间合计。"""
        await _refresh(session, tenant_a, M_LO, M_HI)
        await session.execute(
            text("DELETE FROM report_summary_coverage WHERE tenant_id = :t AND stat_date = :d"),
            {"t": tenant_a.id, "d": D2},
        )
        await session.commit()
        fresh = await SummaryReadRepository(session).freshness(tenant_a.id, M_LO, M_HI)
        assert fresh.source == "live"
        assert fresh.data_as_of is None

    async def test_range_beyond_coverage_falls_back_to_live(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        await _refresh(session, tenant_a, M_LO, M_HI)
        repo = SummaryReadRepository(session)
        assert (await repo.freshness(tenant_a.id, M_LO, M_HI + timedelta(days=1))).source == "live"
        assert (await repo.freshness(tenant_a.id, M_LO - timedelta(days=1), M_HI)).source == "live"

    async def test_kill_switch_forces_live(
        self, session: AsyncSession, tenant_a: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _refresh(session, tenant_a, M_LO, M_HI)
        monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
        fresh = await SummaryReadRepository(session).freshness(tenant_a.id, M_LO, M_HI)
        assert fresh.source == "live"

    async def test_other_tenant_coverage_does_not_count(
        self, session: AsyncSession, tenant_a: Any, tenant_b: Any
    ) -> None:
        """覆盖记录按租户算 —— B 刷过不代表 A 能读汇总表。"""
        await _refresh(session, tenant_b, M_LO, M_HI)
        fresh = await SummaryReadRepository(session).freshness(tenant_a.id, M_LO, M_HI)
        assert fresh.source == "live"
