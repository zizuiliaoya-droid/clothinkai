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
from typing import Any, Literal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
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
from app.modules.report.advanced_repository import (
    PRODUCTION_COUNT_COLUMNS,
    ProductionRepository,
    StoreDailyRepository,
    WorkProgressRepository,
)
from app.modules.report.advanced_schemas import (
    ProductionRow,
    PrWorkProgress,
    StoreReportRow,
    WorkProgressGoodsPage,
    WorkProgressMetrics,
    WorkProgressWeekRow,
    WorkProgressWeeks,
)
from app.modules.report.bi_service import BiService
from app.modules.report.domain import bucket_start
from app.modules.report.drill_service import DrillService
from app.modules.report.pr_scope import PR_SCOPE_ALL, UNASSIGNED, PrScope, PrScopeKind
from app.modules.report.production_service import ProductionService
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.summary_models import PR_PROGRESS_METRICS, PrWorkProgressSummary
from app.modules.report.summary_read import SummaryReadRepository
from app.modules.report.summary_refresh_service import SummaryRefreshService
from app.modules.report.work_progress_models import StoreDaily
from app.modules.report.work_progress_service import WorkProgressService
from app.modules.urge.service import UrgeService

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
    return {
        "goods_a": goods_a,
        "goods_b": goods_b,
        "pr1": pr1,
        "pr2": pr2,
        "style_a": style_a,
        "style_b1": style_b1,
        "blogger": blogger,
    }


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
    seasons: list[str] | None = None,
) -> list[PrWorkProgress]:
    return await svc.get_for_range(
        tenant.id, lo, hi, seasons=seasons, scope=scope, field_ctx=_SUPER_CTX
    )


async def _wpg(
    svc: WorkProgressService,
    tenant: Any,
    lo: date,
    hi: date,
    *,
    scope: PrScope = PR_SCOPE_ALL,
    seasons: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> WorkProgressGoodsPage:
    return await svc.get_goods(
        tenant.id,
        lo,
        hi,
        seasons=seasons,
        scope=scope,
        field_ctx=_SUPER_CTX,
        limit=limit,
        offset=offset,
    )


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

    async def test_summary_equals_live_with_margin(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """毛利三列（``show_margin=True``，管理员）在汇总路径上也要算出来、与实时逐字等（R3 评审 L3）。

        成本读取时现算、不进汇总表；防「只在实时分支挂毛利」——那样汇总路径三列全是 None。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            # 两个商品的启用成员都补上成本：A 单品 30，套装 B 两款各 30 → 60
            await session.execute(
                update(GoodsStyleItem)
                .where(
                    GoodsStyleItem.goods_main_id.in_([seeded["goods_a"].id, seeded["goods_b"].id])
                )
                .values(single_goods_cost=Decimal("30.00"))
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            svc = ProductionService(session)
            via_summary = await svc.get_report(tenant_a.id, (M_LO, M_HI), show_margin=True)
            assert recorder.calls[0] == ("production", "summary")
            via_live = await svc.get_report(
                tenant_a.id, (M_LO, M_HI), use_summary=False, show_margin=True
            )
            assert ("production", "live") in recorder.calls
            assert _dump(via_summary) == _dump(via_live)
            # 场景有效性：两行毛利三列都有非零值（不是 None 和 None 比）
            items = via_summary.items
            assert len(items) == 2
            for row in items:
                assert row.goods_cost_total not in (None, Decimal("0"))
                assert row.gross_profit not in (None, Decimal("0"))
                assert row.gross_margin_rate not in (None, Decimal("0"))
            by_goods = {r.goods_id: r for r in items}
            assert by_goods[seeded["goods_a"].id].goods_cost_total == Decimal("300.00")
            assert by_goods[seeded["goods_b"].id].goods_cost_total == Decimal("300.00")
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


# 投产行的冻结键集合：R3 起一行 = （商品, 渠道），多了 channel 与 link_count，
# 以及 S2 的支付件数 / 站内加购 / 站内加购成本 / 站外加购 / 退货前投产比、S3 的近 7 天三列
_PRODUCTION_ROW_KEYS = {
    "goods_id",
    "goods_code",
    "goods_title",
    "goods_short_name",
    "is_suit",
    "style_codes",
    "main_image_url",
    "channel",
    "link_count",
    "pay_amount",
    "refund_amount",
    "return_rate",
    "confirmed_amount",
    "promo_cost",
    "ad_spend",
    "total_spend",
    "pay_orders",
    "add_cart_count",
    "add_cart_cost",
    "ad_add_cart_count",
    "ad_add_cart_cost",
    "offsite_add_cart",
    "pre_return_roi",
    "net_roi",
    "unit_deal_cost",
    "recent7_pay_amount",
    "recent7_total_spend",
    "recent7_net_roi",
    "goods_cost_total",
    "gross_profit",
    "gross_margin_rate",
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


def _channel_tuple(row: Any) -> tuple[Any, ...]:
    return (str(row.goods_id), row.channel, row.link_count, *_money_tuple(row)[1:])


def _period_calls(recorder: _SourceRecorder) -> list[tuple[str, str]]:
    return [c for c in recorder.calls if c[0] != "production_recent7"]


class TestProductionChannels:
    """7e R3：投产接口按（商品, 渠道）出行；内部调用方（BI / 企微 / AI）仍按商品合计。"""

    @pytest.mark.parametrize("exclude_brushing", [True, False])
    async def test_service_rows_by_channel_both_paths(
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
            # 近 7 天的数据源随今天而定，另由 TestProductionRecent7 钉，这里只看本期 / 上期
            assert _period_calls(recorder) == [
                ("production", "summary"),
                ("production_previous", "summary"),
            ]
            via_live = await svc.get_report(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=exclude_brushing, use_summary=False
            )
            assert _period_calls(recorder)[2:] == [
                ("production", "live"),
                ("production_previous", "live"),
            ]
            assert _dump(via_summary) == _dump(via_live)

            a_id, b_id = str(seeded["goods_a"].id), str(seeded["goods_b"].id)
            # 一行 = （商品, 渠道）：同一商品两行相邻、普通在前；顺序先看商品级支付额
            keys = [(str(r.goods_id), r.channel) for r in via_summary.items]
            assert keys == [(b_id, "普通"), (a_id, "普通"), (a_id, "直播")]
            assert len(set(keys)) == len(keys)
            # N 条链接 = 该渠道启用的千牛链接数；停用的那条普通链接不算
            assert [r.link_count for r in via_summary.items] == [1, 1, 1]
            # 停用链接数（S5）：那条停用的普通链接 D1 有日报 → 1，两路同值（上面逐字比已含）
            assert via_summary.inactive_link_count == 1
            assert via_summary.previous is not None
            prev = [
                (r.channel, r.pay_amount) for r in via_summary.previous if str(r.goods_id) == a_id
            ]
            assert prev == [("普通", Decimal("200.00")), ("直播", Decimal("100.00"))]
        finally:
            tenant_id_ctx.reset(tok)

    async def test_split_rows_and_goods_rows_keep_old_numbers(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """拆行后各渠道的数；``get_goods_rows`` 仍是 R2 的按商品口径（只扣停用链接）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_channels(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            a, b = seeded["goods_a"], seeded["goods_b"]
            a_id, b_id = str(a.id), str(b.id)
            promo = seeded["promo_cost"]
            zero = Decimal("0.00")

            svc = ProductionService(session)
            via_summary = await svc.get_report(tenant_a.id, (M_LO, M_HI))
            assert recorder.calls[0] == ("production", "summary")
            assert set(ProductionRow.model_fields) == _PRODUCTION_ROW_KEYS
            assert all(set(r.model_dump()) == _PRODUCTION_ROW_KEYS for r in via_summary.items)
            assert [_channel_tuple(r) for r in via_summary.items] == [
                (b_id, "普通", 1, Decimal("2000.00"), Decimal("100.00"), 5, zero, zero),
                # 1000 + 600 − 刷单 200；推广与刷单都记普通行
                (
                    a_id,
                    "普通",
                    1,
                    Decimal("1400.00"),
                    Decimal("70.00"),
                    11,
                    promo,
                    Decimal("80.00"),
                ),
                # 直播整段只有退款
                (a_id, "直播", 1, zero, Decimal("25.00"), 2, zero, zero),
            ]

            # 内部调用方：按商品合计、不带渠道，数字 = R2 的旧结构
            old = [
                (b_id, Decimal("2000.00"), Decimal("100.00"), 5, zero, zero),
                (a_id, Decimal("1400.00"), Decimal("95.00"), 13, promo, Decimal("80.00")),
            ]
            goods_summary = await svc.get_goods_rows(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=True, use_summary=True
            )
            assert recorder.calls[-1] == ("production", "summary")
            goods_live = await svc.get_goods_rows(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=True, use_summary=False
            )
            assert recorder.calls[-1] == ("production", "live")
            assert _dump(goods_summary) == _dump(goods_live)
            assert [_money_tuple(r) for r in goods_summary] == old
            assert {(r.channel, r.link_count) for r in goods_summary} == {(None, 0)}

            # 直播链接改成普通：A 合成一行「2 条链接」，数字 = 原来按商品的合计
            await session.execute(
                update(PlatformProduct)
                .where(PlatformProduct.id == seeded["qn_live"].id)
                .values(channel="普通")
            )
            await session.commit()
            flipped = await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False)
            assert [_channel_tuple(r) for r in flipped.items] == [
                (b_id, "普通", 1, *old[0][1:]),
                (a_id, "普通", 2, *old[1][1:]),
            ]
            assert _dump(
                await svc.get_goods_rows(
                    tenant_a.id, (M_LO, M_HI), exclude_brushing=True, use_summary=False
                )
            ) == _dump(goods_live)

            # 启用停用链接：A 多出它那一天的支付 300 / 退款 10 / 加购 3，链接数 3
            await session.execute(
                update(PlatformProduct)
                .where(PlatformProduct.id == seeded["qn_off"].id)
                .values(is_active=True)
            )
            await session.commit()
            enabled = await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False)
            assert via_summary.inactive_link_count == 1
            assert enabled.inactive_link_count == 0
            assert [_channel_tuple(r) for r in enabled.items] == [
                (b_id, "普通", 1, *old[0][1:]),
                (
                    a_id,
                    "普通",
                    3,
                    Decimal("1700.00"),
                    Decimal("105.00"),
                    16,
                    promo,
                    Decimal("80.00"),
                ),
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

    async def test_streaming_ad_spend_with_normal_sales_split(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """R2 评审 Not tested：直播万相台花费 + 普通千牛销售 → 两行各自的数，两路逐字相等。

        商品只有普通千牛（D1 支付 500 / 退款 20 / 加购 4）和直播万相台（D1 花费 60、站内加购 6）。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code=f"XS{uuid4().hex[:6]}")
            goods = await _goods(
                session, tenant_a, style, code=f"G_XS_{uuid4().hex[:5]}", season="春夏"
            )
            qn = await _pp(session, tenant_a, style, goods)
            ad = await _pp(session, tenant_a, style, goods, platform="万相台", channel="直播")
            session.add_all(
                [
                    _qn(tenant_a, qn, D1, "500.00", refund="20.00", add_cart=4, pay_orders=3),
                    AdDaily(
                        tenant_id=tenant_a.id,
                        platform_product_id=ad.id,
                        platform_id_snapshot=ad.platform_id,
                        date=D1,
                        cost=Decimal("60.00"),
                        add_cart_count=6,
                        extra={},
                    ),
                ]
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = ProductionService(session)
            via_summary = await svc.get_report(tenant_a.id, (M_LO, M_HI))
            assert recorder.calls[0] == ("production", "summary")
            via_live = await svc.get_report(tenant_a.id, (M_LO, M_HI), use_summary=False)
            assert ("production", "live") in recorder.calls
            assert _dump(via_summary) == _dump(via_live)

            gid, zero = str(goods.id), Decimal("0.00")
            # 普通行只有销售，直播行只有站内花费（直播没有千牛链接 → 链接数 0）
            assert [_channel_tuple(r) for r in via_summary.items] == [
                (gid, "普通", 1, Decimal("500.00"), Decimal("20.00"), 4, zero, zero),
                (gid, "直播", 0, zero, zero, 0, zero, Decimal("60.00")),
            ]
            assert [(r.pay_orders, r.ad_add_cart_count) for r in via_summary.items] == [
                (3, 0),
                (0, 6),
            ]
            # 两个渠道各自没有配对日 → 站外加购都是 null
            assert [r.offsite_add_cart for r in via_summary.items] == [None, None]

            # 内部调用方按商品合计：一行，销售与花费合在一起
            goods_rows = await svc.get_goods_rows(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=True, use_summary=False
            )
            assert [_money_tuple(r) for r in goods_rows] == [
                (gid, Decimal("500.00"), Decimal("20.00"), 4, zero, Decimal("60.00"))
            ]
        finally:
            tenant_id_ctx.reset(tok)

    @pytest.mark.parametrize("exclude_brushing", [True, False])
    async def test_streaming_only_goods_with_brushing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        recorder: _SourceRecorder,
        exclude_brushing: bool,
    ) -> None:
        """简报第 3 节：只有直播链接的商品有刷单 → 「普通（只有刷单）+ 直播」两行，两路逐字相等。

        商品只有一条直播千牛链接（D1 支付 500 / 退款 20 / 加购 5 / 3 件），D1 刷单 200。
        刷单记普通行（设计 2.3）：剔刷单时普通行支付 −200、不剔时 0；普通行没有千牛链接。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code=f"SO{uuid4().hex[:6]}")
            goods = await _goods(
                session, tenant_a, style, code=f"G_SO_{uuid4().hex[:5]}", season="春夏"
            )
            qn_live = await _pp(session, tenant_a, style, goods, channel="直播")
            session.add_all(
                [
                    _qn(tenant_a, qn_live, D1, "500.00", refund="20.00", add_cart=5, pay_orders=3),
                    OrderAdjustment(
                        tenant_id=tenant_a.id,
                        order_type="刷单",
                        style_id=style.id,
                        amount=Decimal("200.00"),
                        order_date=D1,
                        exclude_from_roi=True,
                        status="待付款",
                    ),
                ]
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = ProductionService(session)
            via_summary = await svc.get_report(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=exclude_brushing
            )
            assert recorder.calls[0] == ("production", "summary")
            via_live = await svc.get_report(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=exclude_brushing, use_summary=False
            )
            assert ("production", "live") in recorder.calls
            assert _dump(via_summary) == _dump(via_live)

            gid, zero = str(goods.id), Decimal("0.00")
            normal_pay = Decimal("-200.00") if exclude_brushing else zero
            # 场景有效性：两行、键唯一、普通在前；剔刷单时普通行支付为负，直播行有销售
            keys = [(str(r.goods_id), r.channel) for r in via_summary.items]
            assert keys == [(gid, "普通"), (gid, "直播")]
            assert len(set(keys)) == len(keys)
            assert [_channel_tuple(r) for r in via_summary.items] == [
                (gid, "普通", 0, normal_pay, zero, 0, zero, zero),
                (gid, "直播", 1, Decimal("500.00"), Decimal("20.00"), 5, zero, zero),
            ]
            assert [r.pay_orders for r in via_summary.items] == [0, 3]

            # 内部调用方按商品合计：一行，支付 = 500 − 刷单
            goods_rows = await svc.get_goods_rows(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=exclude_brushing, use_summary=False
            )
            assert [r.pay_amount for r in goods_rows] == [Decimal("500.00") + normal_pay]
        finally:
            tenant_id_ctx.reset(tok)


async def _seed_add_cart(
    session: AsyncSession, tenant: Any, product_factory: Any
) -> dict[str, Any]:
    """7e R3 S2：新公式与加购（千牛 / 万相台都挂普通 + 直播）。

    商品 A（春夏）：
    - 普通千牛 D1 支付 1000 / 退款 100 / 加购 7 / 4 件，D2 支付 600 / 加购 20 / 4 件；
      普通万相台只有 D2（花费 90、站内加购 9）→ 只配上 D2，站外加购 = 20 − 9 = 11
      （整段区间判配对会把 D1 的 7 也算进去，得 18）；D2 刷单 200（不扣件数）；
    - 直播千牛 D1 支付 300 / 加购 15 / 2 件，D2 加购 2 / 0 件；直播万相台 D1、D2 各花费 30、
      站内加购 10 → 两天配对差 +5 / −8，先求和 −3 再取 0 → 0（逐日取 0 会得 5）。
    商品 B（秋冬）：只有普通千牛 D1 支付 500 / 加购 3 / 2 件 → 没有配对日，站外加购 null。
    """
    style_a = await product_factory.style(style_code=f"AC{uuid4().hex[:6]}")
    goods_a = await _goods(session, tenant, style_a, code=f"G_AC_{uuid4().hex[:5]}", season="春夏")
    qn_norm = await _pp(session, tenant, style_a, goods_a)
    qn_live = await _pp(session, tenant, style_a, goods_a, channel="直播")
    ad_norm = await _pp(session, tenant, style_a, goods_a, platform="万相台")
    ad_live = await _pp(session, tenant, style_a, goods_a, platform="万相台", channel="直播")
    style_b = await product_factory.style(style_code=f"BC{uuid4().hex[:6]}")
    goods_b = await _goods(session, tenant, style_b, code=f"G_BC_{uuid4().hex[:5]}", season="秋冬")
    qn_b = await _pp(session, tenant, style_b, goods_b)

    def ad(pp: PlatformProduct, day: date, cost: str, cart: int) -> AdDaily:
        return AdDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            cost=Decimal(cost),
            add_cart_count=cart,
            extra={},
        )

    session.add_all(
        [
            _qn(tenant, qn_norm, D1, "1000.00", refund="100.00", add_cart=7, pay_orders=4),
            _qn(tenant, qn_norm, D2, "600.00", refund="0.00", add_cart=20, pay_orders=4),
            ad(ad_norm, D2, "90.00", 9),
            _qn(tenant, qn_live, D1, "300.00", refund="0.00", add_cart=15, pay_orders=2),
            _qn(tenant, qn_live, D2, "0.00", refund="0.00", add_cart=2, pay_orders=0),
            ad(ad_live, D1, "30.00", 10),
            ad(ad_live, D2, "30.00", 10),
            _qn(tenant, qn_b, D1, "500.00", refund="0.00", add_cart=3, pay_orders=2),
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
    await session.flush()
    return {"goods_a": goods_a, "goods_b": goods_b}


def _add_cart_tuple(row: Any) -> tuple[Any, ...]:
    return (
        str(row.goods_id),
        row.channel,
        row.pay_orders,
        row.ad_add_cart_count,
        row.ad_add_cart_cost,
        row.offsite_add_cart,
        row.pre_return_roi,
        row.unit_deal_cost,
    )


class TestProductionAddCart:
    """7e R3 S2：退货前投产比、推广单件成交成本（÷ 支付件数）、站内 / 站外加购。"""

    @pytest.mark.parametrize("exclude_brushing", [True, False])
    async def test_new_metrics_both_paths(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        recorder: _SourceRecorder,
        exclude_brushing: bool,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_add_cart(session, tenant_a, product_factory)
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = ProductionService(session)
            via_summary = await svc.get_report(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=exclude_brushing
            )
            assert recorder.calls[0] == ("production", "summary")
            via_live = await svc.get_report(
                tenant_a.id, (M_LO, M_HI), exclude_brushing=exclude_brushing, use_summary=False
            )
            assert ("production", "live") in recorder.calls
            assert _dump(via_summary) == _dump(via_live)

            a_id, b_id = str(seeded["goods_a"].id), str(seeded["goods_b"].id)
            # 普通行支付 1600（剔刷单 1400）/ 总推广成本 90；件数不受刷单开关影响
            normal_roi = Decimal("15.5556") if exclude_brushing else Decimal("17.7778")
            assert [_add_cart_tuple(r) for r in via_summary.items] == [
                (a_id, "普通", 8, 9, Decimal("10.0000"), 11, normal_roi, Decimal("11.2500")),
                (a_id, "直播", 2, 20, Decimal("3.0000"), 0, Decimal("5.0000"), Decimal("30.0000")),
                # 没有站内加购 / 推广成本 / 配对日：以它们为分母的比率与站外加购是 null，
                # 不是 0；单件成交成本分母是件数（2），推广成本 0 → 0
                (b_id, "普通", 2, 0, None, None, None, Decimal("0.0000")),
            ]
            # 场景有效性：每个新列都出现非零值
            items = via_summary.items
            for field in (
                "pay_orders",
                "ad_add_cart_count",
                "ad_add_cart_cost",
                "offsite_add_cart",
                "pre_return_roi",
                "unit_deal_cost",
            ):
                assert any(getattr(r, field) for r in items), field
        finally:
            tenant_id_ctx.reset(tok)


# 7e R3 S3：近 7 天 = T-7 ~ T-1（今天不算），不随筛选区间变
R7_LO, R7_HI = TODAY - timedelta(days=7), TODAY - timedelta(days=1)
# 两个不同的筛选区间：都盖住两个商品，但结束日不同（近 7 天若按筛选结束日算就会变）
R7_FILTER_1 = (TODAY - timedelta(days=14), TODAY - timedelta(days=1))
R7_FILTER_2 = (TODAY - timedelta(days=20), TODAY - timedelta(days=3))


async def _seed_recent7(session: AsyncSession, tenant: Any, product_factory: Any) -> dict[str, Any]:
    """近 7 天场景（日期相对今天）。

    商品 A（春夏）：
    - 普通千牛 T-8 支付 50（区间前一天，不算）、T-7 支付 1000 / 退款 100、T-1 支付 200、
      今天支付 999（不算）；普通万相台 T-1 花费 40；T-7 刷单 100；
      → 近 7 天支付 1100（不剔刷单 1200）、总推广成本 40、净投产比 1000 / 40 = 25（1100 / 40 = 27.5）；
    - 直播千牛 T-10 支付 10（两个筛选区间里都有这一行）、T-1 支付 30 → 近 7 天 30 / 0 / null。
    商品 B（秋冬）：只有 T-12 支付 300 → 近 7 天没有数据：0.00 / 0.00 / null。
    """
    style_a = await product_factory.style(style_code=f"R7A{uuid4().hex[:5]}")
    goods_a = await _goods(session, tenant, style_a, code=f"G_R7A_{uuid4().hex[:5]}", season="春夏")
    qn_norm = await _pp(session, tenant, style_a, goods_a)
    qn_live = await _pp(session, tenant, style_a, goods_a, channel="直播")
    ad_norm = await _pp(session, tenant, style_a, goods_a, platform="万相台")
    style_b = await product_factory.style(style_code=f"R7B{uuid4().hex[:5]}")
    goods_b = await _goods(session, tenant, style_b, code=f"G_R7B_{uuid4().hex[:5]}", season="秋冬")
    qn_b = await _pp(session, tenant, style_b, goods_b)

    session.add_all(
        [
            _qn(tenant, qn_norm, R7_LO - timedelta(days=1), "50.00", refund="0.00"),
            _qn(tenant, qn_norm, R7_LO, "1000.00", refund="100.00"),
            _qn(tenant, qn_norm, R7_HI, "200.00", refund="0.00"),
            _qn(tenant, qn_norm, TODAY, "999.00", refund="0.00"),
            _qn(tenant, qn_live, TODAY - timedelta(days=10), "10.00", refund="0.00"),
            _qn(tenant, qn_live, R7_HI, "30.00", refund="0.00"),
            _qn(tenant, qn_b, TODAY - timedelta(days=12), "300.00", refund="0.00"),
            AdDaily(
                tenant_id=tenant.id,
                platform_product_id=ad_norm.id,
                platform_id_snapshot=ad_norm.platform_id,
                date=R7_HI,
                cost=Decimal("40.00"),
                extra={},
            ),
            OrderAdjustment(
                tenant_id=tenant.id,
                order_type="刷单",
                style_id=style_a.id,
                amount=Decimal("100.00"),
                order_date=R7_LO,
                exclude_from_roi=True,
                status="待付款",
            ),
        ]
    )
    await session.flush()
    return {"goods_a": goods_a, "goods_b": goods_b}


def _recent7_tuple(row: Any) -> tuple[Any, ...]:
    return (
        str(row.goods_id),
        row.channel,
        row.recent7_pay_amount,
        row.recent7_total_spend,
        row.recent7_net_roi,
    )


class TestProductionRecent7:
    """7e R3 S3：近 7 天三列 + ``recent7_range``（默 10）。"""

    @pytest.mark.parametrize("exclude_brushing", [True, False])
    async def test_summary_equals_live_hand_computed(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        recorder: _SourceRecorder,
        exclude_brushing: bool,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_recent7(session, tenant_a, product_factory)
            await session.commit()
            await _refresh(session, tenant_a, R7_FILTER_2[0], R7_HI)

            svc = ProductionService(session)
            via_summary = await svc.get_report(
                tenant_a.id, R7_FILTER_1, exclude_brushing=exclude_brushing
            )
            assert ("production", "summary") in recorder.calls
            assert ("production_recent7", "summary") in recorder.calls
            via_live = await svc.get_report(
                tenant_a.id, R7_FILTER_1, exclude_brushing=exclude_brushing, use_summary=False
            )
            assert ("production_recent7", "live") in recorder.calls
            assert _dump(via_summary) == _dump(via_live)

            assert _dump(via_summary)["recent7_range"] == {
                "date_from": R7_LO.isoformat(),
                "date_to": R7_HI.isoformat(),
            }
            a_id, b_id = str(seeded["goods_a"].id), str(seeded["goods_b"].id)
            zero = Decimal("0.00")
            if exclude_brushing:
                normal = (Decimal("1100.00"), Decimal("40.00"), Decimal("25.0000"))
            else:
                normal = (Decimal("1200.00"), Decimal("40.00"), Decimal("27.5000"))
            assert [_recent7_tuple(r) for r in via_summary.items] == [
                (a_id, "普通", *normal),
                # 没有推广成本：净投产比是 null，不是 0
                (a_id, "直播", Decimal("30.00"), zero, None),
                # 近 7 天没有数据：金额 0.00、比率 null
                (b_id, "普通", zero, zero, None),
            ]
            # 上一期行不带近 7 天
            assert via_summary.previous is not None
            for prev_row in via_summary.previous:
                assert _recent7_tuple(prev_row)[2:] == (None, None, None)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_range_does_not_follow_filter(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """两个不同的筛选区间：``recent7_range`` 与三列都一样。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_recent7(session, tenant_a, product_factory)
            await session.commit()

            svc = ProductionService(session)
            one = await svc.get_report(tenant_a.id, R7_FILTER_1, use_summary=False)
            two = await svc.get_report(tenant_a.id, R7_FILTER_2, use_summary=False)
            # 场景有效性：两个区间的本期数确实不同
            assert [r.pay_amount for r in one.items] != [r.pay_amount for r in two.items]
            assert _dump(one)["recent7_range"] == _dump(two)["recent7_range"]
            assert [_recent7_tuple(r) for r in one.items] == [_recent7_tuple(r) for r in two.items]
            assert one.items[0].recent7_pay_amount == Decimal("1100.00")
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

    @pytest.mark.parametrize("granularity", ["day", "week"])
    @pytest.mark.parametrize("exclude_brushing", [True, False])
    async def test_repository_trend_rows_equal_with_062_columns(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        granularity: str,
        exclude_brushing: bool,
    ) -> None:
        """R2 遗留 L2：汇总趋势带上实时已有的 5 列，仓储层两路逐行相等（实时多一列 net_roi）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_add_cart(session, tenant_a, product_factory)
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            kwargs = {
                "tenant_id": tenant_a.id,
                "goods_id": seeded["goods_a"].id,
                "date_from": M_LO,
                "date_to": M_HI,
                "granularity": granularity,
                "exclude_brushing": exclude_brushing,
            }
            live = await ProductionRepository(session).daily_trend_by_goods(**kwargs)
            summary = await SummaryReadRepository(session).production_trend(**kwargs)
            assert live, "没有趋势点，场景失效"
            live_rows = [
                _norm_repo_row({k: v for k, v in r.items() if k != "net_roi"}) for r in live
            ]
            summary_rows = [_norm_repo_row(r) for r in summary]
            # 列集合与顺序都一致（漏一列这里就红）
            assert [list(r) for r in summary_rows] == [list(r) for r in live_rows]
            assert summary_rows == live_rows
            # 场景有效性：5 列都出现非零值；普通 D2 配对 1 天 + 直播 D1、D2 配对 2 天
            for col in (
                "pay_orders",
                "ad_add_cart_count",
                "paired_all_cart",
                "paired_ad_cart",
                "paired_days",
            ):
                assert any(r[col] for r in summary_rows), col
            assert sum(r["paired_days"] for r in summary_rows) == 3
            assert sum(r["pay_orders"] for r in summary_rows) == 10
        finally:
            tenant_id_ctx.reset(tok)


# 工作进度 R2 新列：金额（两位小数）与其余计数
_WP_MONEY = frozenset(
    {
        "cost",
        "quote_amount",
        "published_quote_amount",
        "effective_quote_amount",
        "unpublished_quote_amount",
        "cancelled_quote_amount",
        "promo_cost",
    }
)


async def _seed_wp_amounts(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    promotion_factory: Any,
    factory: Any,
    seeded: dict[str, Any],
) -> Any:
    """在 ``_seed`` 之上加一个 pr3，只放金额 / 发货 / 季节各列的手算单（不进 ``_seed``，免得动投产的数）。

    goods_a = 春夏、goods_b = 秋冬（套装，含 style_b1）；orphan 款式不在任何商品里。
    """
    pr3 = await factory.user(tenant)
    orphan = await product_factory.style(style_code=f"EO{uuid4().hex[:6]}")

    async def promo(style: Any, **kw: Any) -> None:
        kw.setdefault("internal_code", f"DW{uuid4().hex[:10].upper()}")
        await promotion_factory.promotion(style=style, blogger=seeded["blogger"], pr=pr3, **kw)

    # ① 款式在 goods_a，但单子显式记了 goods_b → 归属秋冬（COALESCE 先看单子自己的商品）
    await promo(
        seeded["style_a"],
        goods_main_id=seeded["goods_b"].id,
        cooperation_date=D2,
        publish_status="已发布",
        ship_status="已发货",
        quote_amount=Decimal("300.00"),
        cost_snapshot=Decimal("20.00"),
    )
    # ② 已发货但还没发布（推定归属 goods_a，春夏）
    await promo(
        seeded["style_a"],
        cooperation_date=D3,
        ship_status="已发货",
        quote_amount=Decimal("150.00"),
    )
    # ③ 待打单不算已发货
    await promo(
        seeded["style_a"],
        goods_main_id=seeded["goods_a"].id,
        cooperation_date=D1,
        ship_status="待打单",
        quote_amount=Decimal("120.00"),
    )
    # ④ 未发布且召回成功、运费 30：推广成本照算（默 8 / 默 17）
    await promo(
        seeded["style_a"],
        goods_main_id=seeded["goods_a"].id,
        cooperation_date=D2,
        recall_status="召回成功",
        return_shipping_fee=Decimal("30.00"),
        quote_amount=Decimal("100.00"),
    )
    # ⑤ 已取消（推定归属 goods_b，秋冬）
    await promo(
        seeded["style_b1"],
        cooperation_date=D3,
        publish_status="已取消",
        quote_amount=Decimal("80.00"),
    )
    # ⑥ 没归属商品：按 PR 汇总照算，选了季节不算
    await promo(orphan, cooperation_date=D1, quote_amount=Decimal("70.00"))
    # ⑦ 已删除（is_active = false）：哪一列都不进
    await promo(
        seeded["style_a"],
        goods_main_id=seeded["goods_a"].id,
        cooperation_date=D1,
        publish_status="已发布",
        ship_status="已发货",
        is_active=False,
        quote_amount=Decimal("999.00"),
    )
    await session.flush()
    return pr3


def _norm_wp(row: Any) -> dict[str, Any]:
    """仓储行 → 可逐字比的 dict：金额两位小数转字符串，其余计数转 int。"""
    out: dict[str, Any] = {"pr_id": row["pr_id"], "pr_name": row["pr_name"]}
    for c in PR_PROGRESS_METRICS:
        v = row[c]
        out[c] = str(Decimal(str(v)).quantize(Decimal("0.01"))) if c in _WP_MONEY else int(v)
    return out


class TestWorkProgress:
    @pytest.mark.parametrize(
        "seasons", [None, ["春夏"], ["春夏", "秋冬"]], ids=["no_filter", "spring", "both"]
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
        monkeypatch: pytest.MonkeyPatch,
        seasons: list[str] | None,
    ) -> None:
        """service 两路逐字相等（含 R5 新列与 PRD 公式列），季节在两路同一口径。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            pr3 = await _seed_wp_amounts(
                session, tenant_a, product_factory, promotion_factory, factory, seeded
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = WorkProgressService(session)
            via_summary = await _wp(svc, tenant_a, M_LO, M_HI, seasons=seasons)
            assert recorder.calls == [("work_progress", "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await _wp(svc, tenant_a, M_LO, M_HI, seasons=seasons)
            assert recorder.calls[1:] == [("work_progress", "live")]
            assert _dump(via_summary) == _dump(via_live)

            by_pr = {r.pr_id: r for r in via_summary}
            if seasons == ["春夏"]:
                # pr3 只剩 ② ③ ④（季节在 SQL 里过滤，不是取全部后在 service 筛）
                assert (by_pr[pr3.id].quote_count, by_pr[pr3.id].promo_cost) == (
                    3,
                    Decimal("400.00"),
                )
                return
            if seasons is not None:
                assert by_pr[pr3.id].quote_count == 5  # ⑥ 没归属商品不算
                return

            # 场景有效性：每个指标字段（含 R5 新列与公式列）都要在某一行出现非零值
            metric_fields = [
                c for c in PrWorkProgress.model_fields if c not in ("pr_id", "pr_name")
            ]
            for c in metric_fields:
                assert any(getattr(r, c) for r in via_summary), f"场景没有产生非零的 {c}"

            # 抖音折算：两天各 1.5，合计 3（逐日取整会得到 4）
            assert by_pr[seeded["pr2"].id].like_count == 3
            # 未分配 PR 的那一行在
            assert None in by_pr
            # pr3 手算（单据见 _seed_wp_amounts）：已发布服务费 300、发布 1 篇；
            # 约稿服务费 820、有效 640；已发货 2、其中已发布 1；推广成本 870、没有点赞 → CPL null
            row = by_pr[pr3.id]
            assert (
                row.avg_published_quote,
                row.amount_publish_rate,
                row.total_amount_publish_rate,
                row.ship_publish_rate,
                row.promo_cost,
                row.cpl,
            ) == (
                Decimal("300.00"),
                Decimal("0.3659"),
                Decimal("0.4688"),
                Decimal("0.5000"),
                Decimal("870.00"),
                None,
            )
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
        """只看本人（read_own → ``PrScope(PR, 本人)``）与「未分配」：下推到 SQL（R2），两路逐字相等。"""
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

    async def test_repo_rows_equal_live_by_season_and_pr(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
    ) -> None:
        """仓储层：实时 ``aggregate_by_pr(by_goods=False)`` ⇄ 汇总 ``work_progress_by_pr``，
        23 列逐行相等（季节 × pr_id 各组合，含下推的 ``UNASSIGNED``）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            pr3 = await _seed_wp_amounts(
                session, tenant_a, product_factory, promotion_factory, factory, seeded
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            live_repo = WorkProgressRepository(session)
            summary = SummaryReadRepository(session)
            thresholds = await UrgeService(session).get_urge_thresholds(tenant_a.id)
            for seasons in (None, ["春夏"], ["秋冬"], ["春夏", "秋冬"]):
                for pr_id in (None, pr3.id, UNASSIGNED):
                    live = await live_repo.aggregate_by_pr(
                        tenant_id=tenant_a.id,
                        date_from=M_LO,
                        date_to=M_HI,
                        today=TODAY,
                        thresholds=thresholds,
                        by_goods=False,
                        seasons=seasons,
                        pr_id=pr_id,
                    )
                    via_summary = await summary.work_progress_by_pr(
                        tenant_id=tenant_a.id,
                        date_from=M_LO,
                        date_to=M_HI,
                        seasons=seasons,
                        pr_id=pr_id,
                    )
                    case = f"seasons={seasons} pr_id={pr_id}"
                    assert [_norm_wp(r) for r in via_summary] == [_norm_wp(r) for r in live], case
                    # 场景有效性：指定 PR / 未分配时只剩那一个 pr_id（未分配的单归属春夏）
                    ids = [r["pr_id"] for r in live]
                    if pr_id == pr3.id:
                        assert ids == [pr3.id], case
                    elif pr_id == UNASSIGNED:
                        assert ids == ([] if seasons == ["秋冬"] else [None]), case
                    else:
                        assert len(live) >= 3, case

                    if seasons is None and pr_id is None:
                        # 4 行（pr1 / pr2 / pr3 / 未分配），23 列每一列都要在某一行非零
                        assert len(live) == 4
                        for c in PR_PROGRESS_METRICS:
                            assert any(r[c] for r in live), f"场景没有产生非零的 {c}"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_amount_and_ship_columns_hand_computed(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
    ) -> None:
        """pr3 的 9 个新列逐个手算；季节只算归属命中的单；刷新按（PR, 商品）落盘。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            pr3 = await _seed_wp_amounts(
                session, tenant_a, product_factory, promotion_factory, factory, seeded
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            repo = WorkProgressRepository(session)
            thresholds = await UrgeService(session).get_urge_thresholds(tenant_a.id)

            async def pr3_row(seasons: list[str] | None) -> dict[str, Any]:
                rows = await repo.aggregate_by_pr(
                    tenant_id=tenant_a.id,
                    date_from=M_LO,
                    date_to=M_HI,
                    today=TODAY,
                    thresholds=thresholds,
                    by_goods=False,
                    seasons=seasons,
                    pr_id=pr3.id,
                )
                assert len(rows) == 1
                return _norm_wp(rows[0])

            got = await pr3_row(None)
            expected_all = {
                "quote_count": 6,
                "quote_amount": "820.00",  # 300 + 150 + 120 + 100 + 80 + 70（已删除的 999 不算）
                "published_quote_amount": "300.00",
                "effective_quote_amount": "640.00",  # 扣召回 ④ 与取消 ⑤
                "unpublished_quote_amount": "440.00",  # ② ③ ④ ⑥
                "cancelled_quote_amount": "80.00",
                # 应付 = 服务费 + 样品 + 运费，不看发布 / 召回：含 ④ 的 100 + 30
                "promo_cost": "870.00",
                "unpublished_count": 4,
                "shipped_count": 2,  # ① ②（③ 待打单不算）
                "shipped_published_count": 1,  # ①
            }
            assert {k: got[k] for k in expected_all} == expected_all

            spring = await pr3_row(["春夏"])  # ② ③ ④
            assert (
                spring["quote_count"],
                spring["quote_amount"],
                spring["promo_cost"],
                spring["shipped_count"],
                spring["shipped_published_count"],
            ) == (3, "370.00", "400.00", 1, 0)
            autumn = await pr3_row(["秋冬"])  # ①（显式归属压过款式推定）⑤
            assert (
                autumn["quote_count"],
                autumn["quote_amount"],
                autumn["promo_cost"],
                autumn["shipped_count"],
                autumn["shipped_published_count"],
                autumn["cancelled_quote_amount"],
            ) == (2, "380.00", "400.00", 1, 1, "80.00")
            assert (await pr3_row(["春夏", "秋冬"]))["quote_count"] == 5  # ⑥ 没归属商品不算

            # by_goods=True：按（PR, 归属商品）出行，没归属商品的单落在 NULL 行、排最后
            by_goods = await repo.aggregate_by_pr(
                tenant_id=tenant_a.id,
                date_from=M_LO,
                date_to=M_HI,
                today=TODAY,
                thresholds=thresholds,
                by_goods=True,
                pr_id=pr3.id,
            )
            goods_a, goods_b = seeded["goods_a"].id, seeded["goods_b"].id
            assert [(r["goods_main_id"], int(r["quote_count"])) for r in by_goods] == [
                (goods_a, 3),
                (goods_b, 2),
                (None, 1),
            ]
            # 刷新落盘带商品维度（同一 PR 一天可能有多行）
            stored = (
                await session.execute(
                    select(
                        PrWorkProgressSummary.goods_main_id,
                        func.sum(PrWorkProgressSummary.quote_count),
                    )
                    .where(
                        PrWorkProgressSummary.tenant_id == tenant_a.id,
                        PrWorkProgressSummary.pr_id == pr3.id,
                    )
                    .group_by(PrWorkProgressSummary.goods_main_id)
                )
            ).all()
            assert {g: int(n) for g, n in stored} == {goods_a: 3, goods_b: 2, None: 1}
        finally:
            tenant_id_ctx.reset(tok)


# 工作进度可相加的列（计数与金额）：单品行 + 未归属 = PR 表合计
_WP_ADDITIVE = tuple(c for c in PR_PROGRESS_METRICS if c != "like_count")


async def _seed_goods_tie(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    factory: Any,
) -> tuple[GoodsMain, GoodsMain]:
    """两个同简称商品、约稿量都是 2：约稿量与显示名都相同，只能靠商品 id 兜底排序。

    id 大的那个是一个 PR 的 2 单（实时 ``aggregate_by_pr`` 按约稿量排在前面），id 小的那个
    分在两个 PR 各 1 单（排在后面）—— 实时的输入顺序与 id 顺序相反，漏了兜底键就会排反。
    返回 ``(id 小的, id 大的)``。
    """
    blogger = await blogger_factory.blogger()
    made: list[tuple[GoodsMain, Any]] = []
    for _ in range(2):
        style = await product_factory.style(style_code=f"ET{uuid4().hex[:6]}")
        goods = await _goods(
            session,
            tenant,
            style,
            code=f"G_T_{uuid4().hex[:5]}",
            season="春夏",
            short_name="同名款",
        )
        made.append((goods, style))
    (lo, lo_style), (hi, hi_style) = sorted(made, key=lambda m: m[0].id)

    async def promo(goods: GoodsMain, style: Any, pr: Any) -> None:
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=pr,
            goods_main_id=goods.id,
            cooperation_date=D2,
            internal_code=f"DT{uuid4().hex[:10].upper()}",
        )

    pr_x, pr_y, pr_z = [await factory.user(tenant) for _ in range(3)]
    await promo(hi, hi_style, pr_x)
    await promo(hi, hi_style, pr_x)
    await promo(lo, lo_style, pr_y)
    await promo(lo, lo_style, pr_z)
    await session.flush()
    return lo, hi


class TestWorkProgressGoods:
    """单品卡片（7e R5，设计 2.5）：汇总 ``work_progress_by_goods`` ⇄ 实时按商品相加。"""

    @pytest.mark.parametrize(
        "seasons", [None, ["春夏"], ["春夏", "秋冬"]], ids=["no_filter", "spring", "both"]
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
        monkeypatch: pytest.MonkeyPatch,
        seasons: list[str] | None,
    ) -> None:
        """两路逐字相等，``record_source`` 每次恰好一条；响应不带商品编码。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await _seed_wp_amounts(
                session, tenant_a, product_factory, promotion_factory, factory, seeded
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)

            svc = WorkProgressService(session)
            via_summary = await _wpg(svc, tenant_a, M_LO, M_HI, seasons=seasons)
            assert recorder.calls == [("work_progress_goods", "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await _wpg(svc, tenant_a, M_LO, M_HI, seasons=seasons)
            assert recorder.calls[1:] == [("work_progress_goods", "live")]
            assert _dump(via_summary) == _dump(via_live)

            dumped = _dump(via_summary)
            assert "goods_code" not in dumped["items"][0]
            goods_a, goods_b = seeded["goods_a"].id, seeded["goods_b"].id
            ids = [r.goods_id for r in via_summary.items]
            if seasons is None:
                # 场景有效性：两个商品 + 未归属（⑥ 一单）
                assert ids == [goods_a, goods_b]
                assert via_summary.unassigned is not None
                assert via_summary.unassigned.display_name == "未归属商品"
                assert via_summary.unassigned.quote_count == 1
                # 每个指标字段（含 R5 新列与公式列）都要在某张卡 / 未归属行出现非零值
                rows = [*via_summary.items, via_summary.unassigned]
                for c in WorkProgressMetrics.model_fields:
                    assert any(getattr(r, c) for r in rows), f"场景没有产生非零的 {c}"
            else:
                # 选了季节：没归属商品的单不算，信封里也没有「未归属商品」
                assert ids == ([goods_a] if seasons == ["春夏"] else [goods_a, goods_b])
                assert via_summary.unassigned is None
            assert via_summary.total == len(ids)
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
        """只看本人（``PrScope(PR, pr1)``）与「未分配」：两路逐字相等，卡片之和 = 同范围的 PR 表那一行。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await _seed_wp_amounts(
                session, tenant_a, product_factory, promotion_factory, factory, seeded
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            goods_a, goods_b = seeded["goods_a"].id, seeded["goods_b"].id
            if who == "pr1":
                scope = PrScope(PrScopeKind.PR, seeded["pr1"].id)
                expected_pr = seeded["pr1"].id
                # pr1：goods_a 8 单（含按款式推定的）、goods_b 1 单
                expected_items = [(goods_a, 8), (goods_b, 1)]
            else:
                scope = PrScope(PrScopeKind.UNASSIGNED)
                expected_pr = None
                expected_items = [(goods_a, 1)]  # 未分配那一单按款式推定归属 goods_a

            svc = WorkProgressService(session)
            via_summary = await _wpg(svc, tenant_a, M_LO, M_HI, scope=scope)
            assert recorder.calls == [("work_progress_goods", "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await _wpg(svc, tenant_a, M_LO, M_HI, scope=scope)
            assert recorder.calls[1:] == [("work_progress_goods", "live")]
            assert _dump(via_summary) == _dump(via_live)

            # 场景有效性：全量里 goods_a 有 12 单，范围内只剩本范围的单；没有未归属商品的单
            assert [(r.goods_id, r.quote_count) for r in via_summary.items] == expected_items
            assert via_summary.unassigned is None
            pr_rows = await _wp(svc, tenant_a, M_LO, M_HI, scope=scope)
            assert [r.pr_id for r in pr_rows] == [expected_pr]
            for c in _WP_ADDITIVE:
                assert sum(getattr(r, c) for r in via_summary.items) == getattr(pr_rows[0], c), c
        finally:
            tenant_id_ctx.reset(tok)

    async def test_pr3_promo_cost_hand_computed(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pr3（单据见 ``_seed_wp_amounts``）：推广成本不看发布 / 召回，含 ④ 未发布召回成功的运费 30。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            pr3 = await _seed_wp_amounts(
                session, tenant_a, product_factory, promotion_factory, factory, seeded
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            svc = WorkProgressService(session)
            scope = PrScope(PrScopeKind.PR, pr3.id)
            goods_a, goods_b = seeded["goods_a"], seeded["goods_b"]

            for reads_enabled in (True, False):
                monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", reads_enabled)
                page = await _wpg(svc, tenant_a, M_LO, M_HI, scope=scope)
                case = f"summary_reads={reads_enabled}"
                got = [
                    (r.goods_id, r.display_name, r.goods_short_name, r.quote_count, r.promo_cost)
                    for r in page.items
                ]
                assert got == [
                    # ② 150 + ③ 120 + ④ 100 + 运费 30
                    (goods_a.id, "A 简称", "A 简称", 3, Decimal("400.00")),
                    # ① 300 + 样品 20、⑤ 80；没填简称 → 显示全称
                    (goods_b.id, goods_b.goods_title, None, 2, Decimal("400.00")),
                ], case
                assert page.unassigned is not None, case
                assert (page.unassigned.quote_count, page.unassigned.promo_cost) == (
                    1,
                    Decimal("70.00"),
                ), case

                spring = await _wpg(svc, tenant_a, M_LO, M_HI, scope=scope, seasons=["春夏"])
                assert [(r.goods_id, r.promo_cost) for r in spring.items] == [
                    (goods_a.id, Decimal("400.00"))
                ], case
                assert spring.unassigned is None, case
        finally:
            tenant_id_ctx.reset(tok)

    async def test_totals_drill_and_rates_after_sum(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """商品行 + 未归属 = PR 表合计；卡片推广成本 = 该商品下钻行之和；跨 PR 合并后比率再除。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await _seed_wp_amounts(
                session, tenant_a, product_factory, promotion_factory, factory, seeded
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            svc = WorkProgressService(session)
            goods_a = seeded["goods_a"]

            for reads_enabled in (True, False):
                monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", reads_enabled)
                case = f"summary_reads={reads_enabled}"
                page = await _wpg(svc, tenant_a, M_LO, M_HI)
                pr_rows = await _wp(svc, tenant_a, M_LO, M_HI)
                assert page.unassigned is not None, case
                goods_rows = [*page.items, page.unassigned]
                for c in _WP_ADDITIVE:
                    assert sum(getattr(r, c) for r in goods_rows) == sum(
                        getattr(r, c) for r in pr_rows
                    ), f"{case} {c}"
                assert sum(r.like_count for r in goods_rows) == sum(r.like_count for r in pr_rows)

                card = next(r for r in page.items if r.goods_id == goods_a.id)
                # goods_a 跨 pr1（8 单，有效 5、发布 1）、pr3（3 单，有效 2）、未分配（1 单）：
                # 合计后 1 ÷ 8，不是首行 pr1 的 1 ÷ 5
                assert (card.quote_count, card.effective_quote_count, card.publish_count) == (
                    12,
                    8,
                    1,
                ), case
                assert card.month_complete_rate == Decimal("0.1250"), case

            drill = await DrillService(session).list_promotions(
                tenant_a.id,
                M_LO,
                M_HI,
                pr_scope=PR_SCOPE_ALL,
                goods_id=goods_a.id,
                seasons=None,
                publish_status=None,
                page=1,
                page_size=100,
                field_ctx=_SUPER_CTX,
            )
            assert drill.total == card.quote_count
            assert card.promo_cost == sum((i.total_promo_cost or Decimal(0)) for i in drill.items)
            # 场景有效性：下钻里确有「未发布且召回成功、运费 30」那一单
            assert any(
                i.publish_status == "未发布"
                and i.recall_status == "召回成功"
                and i.total_promo_cost == Decimal("130.00")
                for i in drill.items
            )
        finally:
            tenant_id_ctx.reset(tok)

    async def test_tie_broken_by_goods_id(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """约稿量、显示名都相同 → 商品 id 升序兜底（两路一致，不依赖 SQL 的行序）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            lo, hi = await _seed_goods_tie(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            svc = WorkProgressService(session)
            for reads_enabled in (True, False):
                monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", reads_enabled)
                page = await _wpg(svc, tenant_a, M_LO, M_HI)
                assert [(r.goods_id, r.display_name, r.quote_count) for r in page.items] == [
                    (lo.id, "同名款", 2),
                    (hi.id, "同名款", 2),
                ], f"summary_reads={reads_enabled}"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_paging_and_total(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
    ) -> None:
        """``total`` 只数有归属的商品，切片在排序之后；「未归属商品」每页都在信封里。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await _seed_wp_amounts(
                session, tenant_a, product_factory, promotion_factory, factory, seeded
            )
            await session.commit()
            await _refresh(session, tenant_a, M_LO, M_HI)
            svc = WorkProgressService(session)
            pages = [await _wpg(svc, tenant_a, M_LO, M_HI, limit=1, offset=o) for o in (0, 1, 2)]
            assert [[r.goods_id for r in p.items] for p in pages] == [
                [seeded["goods_a"].id],
                [seeded["goods_b"].id],
                [],
            ]
            assert [(p.total, p.limit, p.offset) for p in pages] == [
                (2, 1, 0),
                (2, 1, 1),
                (2, 1, 2),
            ]
            assert all(p.unassigned is not None for p in pages)
            assert (pages[0].date_from, pages[0].date_to) == (M_LO, M_HI)
        finally:
            tenant_id_ctx.reset(tok)


# 周明细场景的区间：M_LO 当天或之前的周三 ~ D3 当天或之后的周五（首尾周都被截断）
WEEK_LO = M_LO - timedelta(days=(M_LO.weekday() - 2) % 7)
WEEK_HI = D3 + timedelta(days=(4 - D3.weekday()) % 7)
_WEEK_NON_METRIC = {"week_start", "date_from", "date_to"}


async def _wpw(
    svc: WorkProgressService,
    tenant: Any,
    lo: date,
    hi: date,
    *,
    scope: PrScope = PR_SCOPE_ALL,
    seasons: list[str] | None = None,
) -> WorkProgressWeeks:
    return await svc.get_weeks(
        tenant.id, lo, hi, seasons=seasons, scope=scope, field_ctx=_SUPER_CTX
    )


async def _seed_week_edges(
    session: AsyncSession, promotion_factory: Any, seeded: dict[str, Any]
) -> None:
    """周明细的边界单（在 ``_seed`` + ``_seed_wp_amounts`` 之上）。

    - 首周周一、末周周日各一单：在周内但在区间外，查整周就会多算
    - 区间首日、末日各一单：截断后的首尾周都要算到
    - D2 那周 pr1、未分配各一张抖音单（点赞 1.5）：跨 PR 逐行取整再相加会少 1
    """
    style_a, pr1, pr2 = seeded["style_a"], seeded["pr1"], seeded["pr2"]

    async def promo(**kw: Any) -> None:
        kw.setdefault("internal_code", f"DK{uuid4().hex[:10].upper()}")
        await promotion_factory.promotion(style=style_a, blogger=seeded["blogger"], **kw)

    await promo(pr=pr1, cooperation_date=WEEK_LO - timedelta(days=2), quote_amount=Decimal("11"))
    await promo(pr=pr1, cooperation_date=WEEK_HI + timedelta(days=2), quote_amount=Decimal("12"))
    await promo(pr=pr2, cooperation_date=WEEK_LO, quote_amount=Decimal("21.00"))
    await promo(pr=pr2, cooperation_date=WEEK_HI, quote_amount=Decimal("33.00"))
    for pr in (pr1, None):
        await promo(
            pr=pr, cooperation_date=D2, publish_status="已发布", platform="抖音", like_count=15
        )
    await session.flush()


class TestWorkProgressWeeks:
    """周明细（7e R5，Q15）：汇总 ``work_progress_by_week`` ⇄ 实时逐周调 ``aggregate_by_pr``。"""

    async def _seed_all(
        self,
        session: AsyncSession,
        tenant: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
    ) -> dict[str, Any]:
        seeded = await _seed(
            session, tenant, product_factory, blogger_factory, promotion_factory, factory
        )
        await _seed_wp_amounts(session, tenant, product_factory, promotion_factory, factory, seeded)
        await _seed_week_edges(session, promotion_factory, seeded)
        await session.commit()
        return seeded

    @pytest.mark.parametrize("seasons", [None, ["春夏"]], ids=["no_filter", "spring"])
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
        seasons: list[str] | None,
    ) -> None:
        """两路逐字相等（``record_source`` 每次恰好一条）；首尾周截到区间并标实际起止。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await self._seed_all(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await _refresh(session, tenant_a, WEEK_LO, WEEK_HI)

            svc = WorkProgressService(session)
            via_summary = await _wpw(svc, tenant_a, WEEK_LO, WEEK_HI, seasons=seasons)
            assert recorder.calls == [("work_progress_weeks", "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await _wpw(svc, tenant_a, WEEK_LO, WEEK_HI, seasons=seasons)
            assert recorder.calls[1:] == [("work_progress_weeks", "live")]
            assert _dump(via_summary) == _dump(via_live)

            items = via_summary.items
            assert (via_summary.date_from, via_summary.date_to) == (WEEK_LO, WEEK_HI)
            starts = [r.week_start for r in items]
            assert len(starts) >= 2
            assert starts == sorted(set(starts))
            assert all(s.weekday() == 0 for s in starts)
            for r in items:
                assert (r.date_from, r.date_to) == (
                    max(r.week_start, WEEK_LO),
                    min(r.week_start + timedelta(days=6), WEEK_HI),
                ), r.week_start
            # 场景有效性：首周从周三截起、末周截到周五（区间首尾日都有单）
            assert items[0].date_from == WEEK_LO != items[0].week_start
            assert items[-1].date_to == WEEK_HI != items[-1].week_start + timedelta(days=6)

            if seasons is None:
                metric_fields = [
                    c for c in WorkProgressWeekRow.model_fields if c not in _WEEK_NON_METRIC
                ]
                for c in metric_fields:
                    assert any(getattr(r, c) for r in items), f"场景没有产生非零的 {c}"
        finally:
            tenant_id_ctx.reset(tok)

    @pytest.mark.parametrize("seasons", [None, ["春夏"]], ids=["no_filter", "spring"])
    async def test_week_totals_equal_pr_totals(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        monkeypatch: pytest.MonkeyPatch,
        seasons: list[str] | None,
    ) -> None:
        """K5：同区间同季节，各周计数与金额之和 = PR 表合计（两路都是）；点赞在仓储层比精确和。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await self._seed_all(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await _refresh(session, tenant_a, WEEK_LO, WEEK_HI)
            svc = WorkProgressService(session)

            for reads_enabled in (True, False):
                monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", reads_enabled)
                case = f"summary_reads={reads_enabled}"
                weeks = await _wpw(svc, tenant_a, WEEK_LO, WEEK_HI, seasons=seasons)
                pr_rows = await _wp(svc, tenant_a, WEEK_LO, WEEK_HI, seasons=seasons)
                assert len(weeks.items) >= 2, case
                assert sum(r.quote_count for r in pr_rows) > 0, case
                for c in _WP_ADDITIVE:
                    assert sum(getattr(r, c) for r in weeks.items) == sum(
                        getattr(r, c) for r in pr_rows
                    ), f"{case} {c}"

            # 点赞：页面上每行各自取整，比精确值要在仓储层（K5 SQL 同口径）
            summary = SummaryReadRepository(session)
            by_week = await summary.work_progress_by_week(
                tenant_id=tenant_a.id,
                date_from=WEEK_LO,
                date_to=WEEK_HI,
                seasons=seasons,
                pr_id=None,
            )
            by_pr = await summary.work_progress_by_pr(
                tenant_id=tenant_a.id,
                date_from=WEEK_LO,
                date_to=WEEK_HI,
                seasons=seasons,
                pr_id=None,
            )
            live = await WorkProgressRepository(session).aggregate_by_pr(
                tenant_id=tenant_a.id,
                date_from=WEEK_LO,
                date_to=WEEK_HI,
                today=TODAY,
                thresholds=await UrgeService(session).get_urge_thresholds(tenant_a.id),
                by_goods=False,
                seasons=seasons,
                pr_id=None,
            )

            def likes(rows: Any) -> Decimal:
                return sum((Decimal(str(r["like_count"])) for r in rows), Decimal(0))

            assert likes(by_week) == likes(by_pr) == likes(live)
            # 场景有效性：确有带小数的 PR 行（逐行取整再相加就会差）
            assert any(Decimal(str(r["like_count"])) % 1 for r in live)
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
        """只看本人（``PrScope(PR, pr1)``）与「未分配」：两路逐字相等，各周之和 = 同范围的 PR 表那一行。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await self._seed_all(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await _refresh(session, tenant_a, WEEK_LO, WEEK_HI)
            if who == "pr1":
                scope = PrScope(PrScopeKind.PR, seeded["pr1"].id)
                expected_pr = seeded["pr1"].id
            else:
                scope = PrScope(PrScopeKind.UNASSIGNED)
                expected_pr = None

            svc = WorkProgressService(session)
            via_summary = await _wpw(svc, tenant_a, WEEK_LO, WEEK_HI, scope=scope)
            assert recorder.calls == [("work_progress_weeks", "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await _wpw(svc, tenant_a, WEEK_LO, WEEK_HI, scope=scope)
            assert recorder.calls[1:] == [("work_progress_weeks", "live")]
            assert _dump(via_summary) == _dump(via_live)

            pr_rows = await _wp(svc, tenant_a, WEEK_LO, WEEK_HI, scope=scope)
            everyone = await _wpw(svc, tenant_a, WEEK_LO, WEEK_HI)
            # 场景有效性：范围内只剩那一个 PR，且确实比全量少（范围真的下推了）
            assert [r.pr_id for r in pr_rows] == [expected_pr]
            assert via_summary.items
            scoped_total = sum(r.quote_count for r in via_summary.items)
            assert 0 < scoped_total < sum(r.quote_count for r in everyone.items)
            for c in _WP_ADDITIVE:
                assert sum(getattr(r, c) for r in via_summary.items) == getattr(pr_rows[0], c), c
        finally:
            tenant_id_ctx.reset(tok)

    async def test_partial_coverage_whole_range_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """区间末日没刷过 → 整段走实时（不拼汇总 + 实时），末周照样截到区间止。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await self._seed_all(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            await _refresh(session, tenant_a, WEEK_LO, WEEK_HI - timedelta(days=1))
            svc = WorkProgressService(session)
            weeks = await _wpw(svc, tenant_a, WEEK_LO, WEEK_HI)
            assert recorder.calls == [("work_progress_weeks", "live")]
            assert weeks.items[-1].date_to == WEEK_HI
            pr_rows = await _wp(svc, tenant_a, WEEK_LO, WEEK_HI)
            assert sum(r.quote_count for r in weeks.items) == sum(r.quote_count for r in pr_rows)
        finally:
            tenant_id_ctx.reset(tok)


# 店铺并集场景里有行的日子（相对周三的天数）
_STORE_DAYS = (0, 1, 2, 3, 4, 7, 9)


def _norm_store_row(row: Any) -> dict[str, Any]:
    """店铺日行逐字比：金额两位小数转字符串，NULL 原样（未录 ≠ 0）。"""
    return {
        k: str(v.quantize(Decimal("0.01"))) if isinstance(v, Decimal) else v for k, v in row.items()
    }


async def _seed_store_and_refresh(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> dict[str, Any]:
    """店铺日期并集（r2-H1 数据层）。W = 上月第一个不在 1 号的周三，查询区间 [W, 月末 − 2]。

    - W：普通千牛 1000 / 退款 50 + 未映射日报 200；手填直通车 40、仅退款 10
    - W+1：直播千牛 300 / 退款 30 + 停用链接 100 / 退款 10
    - W+2：只有万相台：普通 60 + 直播 20
    - W+3：只有已发布推广；W+5 的未发布推广不加日子
    - W+4：只有刷单 150；W+6 的不剔除调整不加日子
    - W+7：只有手填仅退款 5
    - 刷新整月之后才录的 W+9 全站推 12（没有其他来源）
    """
    after_first = M_LO + timedelta(days=1)
    wed = after_first + timedelta(days=(2 - after_first.weekday()) % 7)
    hi = M_HI - timedelta(days=2)

    style = await product_factory.style(style_code=f"SD{uuid4().hex[:6]}")
    goods = await _goods(session, tenant, style, code=f"G_SD_{uuid4().hex[:5]}", season="春夏")
    qn_norm = await _pp(session, tenant, style, goods)
    qn_live = await _pp(session, tenant, style, goods, channel="直播")
    qn_off = await _pp(session, tenant, style, goods, is_active=False)
    ad_norm = await _pp(session, tenant, style, goods, platform="万相台")
    ad_live = await _pp(session, tenant, style, goods, platform="万相台", channel="直播")

    def ad(pp: PlatformProduct, day: date, cost: str) -> AdDaily:
        return AdDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            cost=Decimal(cost),
            extra={},
        )

    def adjust(day: date, amount: str, *, exclude: bool) -> OrderAdjustment:
        return OrderAdjustment(
            tenant_id=tenant.id,
            order_type="刷单" if exclude else "拍单",
            style_id=style.id,
            amount=Decimal(amount),
            order_date=day,
            exclude_from_roi=exclude,
            status="待付款",
        )

    w = [wed + timedelta(days=n) for n in range(10)]
    session.add_all(
        [
            _qn(tenant, qn_norm, w[0], "1000.00", refund="50.00"),
            QianniuDaily(
                tenant_id=tenant.id,
                platform_product_id=None,
                platform_id_snapshot=f"UNMAPPED{uuid4().hex[:6]}",
                date=w[0],
                visitors=100,
                pay_amount=Decimal("200.00"),
                pay_orders=5,
            ),
            _qn(tenant, qn_live, w[1], "300.00", refund="30.00"),
            _qn(tenant, qn_off, w[1], "100.00", refund="10.00"),
            ad(ad_norm, w[2], "60.00"),
            ad(ad_live, w[2], "20.00"),
            adjust(w[4], "150.00", exclude=True),
            adjust(w[6], "70.00", exclude=False),
            StoreDaily(
                tenant_id=tenant.id,
                date=w[0],
                zhitongche_spend=Decimal("40.00"),
                refund_only_amount=Decimal("10.00"),
            ),
            StoreDaily(tenant_id=tenant.id, date=w[7], refund_only_amount=Decimal("5.00")),
        ]
    )
    blogger = await blogger_factory.blogger()
    for day, status in ((w[3], "已发布"), (w[5], "未发布")):
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            cooperation_date=day,
            publish_status=status,
            quote_amount=Decimal("400.00"),
            internal_code=f"SD{uuid4().hex[:10].upper()}",
        )
    await session.flush()
    promo_cost = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(total_promo_cost), 0) FROM promotion "
                "WHERE tenant_id = CAST(:t AS uuid) AND publish_status = '已发布'"
            ),
            {"t": str(tenant.id)},
        )
    ).scalar_one()
    assert promo_cost > 0, "推广成本为 0，场景失效"
    await session.commit()
    await _refresh(session, tenant, M_LO, M_HI)

    # 刷新之后才手填、又没有其他来源的日子：汇总日表里没有它，汇总读取要并 store_daily
    session.add(StoreDaily(tenant_id=tenant.id, date=w[9], ad_spend_total=Decimal("12.00")))
    await session.commit()
    return {
        "wed": wed,
        "hi": hi,
        "promo_cost": str(Decimal(promo_cost).quantize(Decimal("0.01"))),
    }


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

    @pytest.mark.parametrize("granularity", ["day", "week"])
    async def test_union_days_service_equal_one_source(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        recorder: _SourceRecorder,
        monkeypatch: pytest.MonkeyPatch,
        granularity: str,
    ) -> None:
        """r2-H1 数据层：区间从周三起、非整月，两路逐字等，一个响应只有一种来源。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_store_and_refresh(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            wed, hi = seeded["wed"], seeded["hi"]
            svc = StoreDailyService(session)
            via_summary = await svc.get_dashboard(tenant_a.id, (wed, hi), granularity)
            assert recorder.calls == [("store_daily", "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await svc.get_dashboard(tenant_a.id, (wed, hi), granularity)
            assert recorder.calls[1:] == [("store_daily", "live")]
            assert _dump(via_summary) == _dump(via_live)

            if granularity == "day":
                # 只有万相台 / 推广 / 刷单 / 手填的日子都在；未发布推广、不剔除的调整不加日子
                assert [r.date for r in via_summary] == [
                    wed + timedelta(days=n) for n in _STORE_DAYS
                ]
                return
            # 周三那周只有周三手填了直通车：周行直通车 = 40，另两项整周没填仍是 None
            first, second = via_summary
            assert first.date == wed - timedelta(days=2)
            assert first.zhitongche_spend == Decimal("40.00")
            assert first.ad_spend_total is None
            assert first.yinli_spend is None
            assert (first.visitors, first.pay_amount, first.pay_orders) == (
                400,
                Decimal("1600.00"),
                20,
            )
            # 下一周只有手填：刷新后才录的直通车总额也在
            assert second.date == wed + timedelta(days=5)
            assert second.ad_spend_total == Decimal("12.00")
            assert second.pay_amount == Decimal("0.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_repository_day_rows_equal_hand_computed(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """仓储层日行两路逐行等（6 列 + 仅退款，未录为 None）；值 = 手算；区间和 = BI 店铺汇总。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_store_and_refresh(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            wed, hi = seeded["wed"], seeded["hi"]
            kwargs = {"tenant_id": tenant_a.id, "date_from": wed, "date_to": hi}
            live = await StoreDailyRepository(session).aggregate(**kwargs)
            summary = await SummaryReadRepository(session).store_daily(**kwargs)
            assert [_norm_store_row(r) for r in summary] == [_norm_store_row(r) for r in live]

            rows = {r["date"]: _norm_store_row(r) for r in live}
            assert sorted(rows) == [wed + timedelta(days=n) for n in _STORE_DAYS]
            promo = seeded["promo_cost"]
            # (日, 访客, 支付, 支付单数, 退款, 刷单, 站内, 站外, 直播支付, 直播站内,
            #  全站推, 直通车, 引力魔方, 仅退款)
            want = {
                0: (200, "1200.00", 10, "50.00", "0.00", "0.00", "0.00", "0.00", "0.00",
                    None, "40.00", None, "10.00"),
                1: (200, "400.00", 10, "40.00", "0.00", "0.00", "0.00", "300.00", "0.00",
                    None, None, None, None),
                2: (0, "0.00", 0, "0.00", "0.00", "80.00", "0.00", "0.00", "20.00",
                    None, None, None, None),
                3: (0, "0.00", 0, "0.00", "0.00", "0.00", promo, "0.00", "0.00",
                    None, None, None, None),
                4: (0, "0.00", 0, "0.00", "150.00", "0.00", "0.00", "0.00", "0.00",
                    None, None, None, None),
                7: (0, "0.00", 0, "0.00", "0.00", "0.00", "0.00", "0.00", "0.00",
                    None, None, None, "5.00"),
                9: (0, "0.00", 0, "0.00", "0.00", "0.00", "0.00", "0.00", "0.00",
                    "12.00", None, None, None),
            }  # fmt: skip
            cols = (
                "visitors", "pay_amount", "pay_orders", "refund_amount", "brushing_amount",
                "ad_spend", "promo_cost", "live_pay_amount", "live_ad_spend",
                "ad_spend_total", "zhitongche_spend", "yinli_spend", "refund_only_amount",
            )  # fmt: skip
            for n, values in want.items():
                got = rows[wed + timedelta(days=n)]
                assert tuple(got[c] for c in cols) == values, f"第 {n} 天"

            # 区间和 = BI 店铺 KPI（R6 起 BI 读这里）；站内逐天套默 13：第 0 天手填直通车 40、
            # 第 2 天万相台 80、第 9 天刷新后才录的全站推 12（不是万相台合计 80）
            dash = await BiService(session).get_dashboard(
                tenant_a.id, (wed, hi), exclude_brushing=True, field_ctx=_SUPER_CTX
            )
            kpi = dash.store.summary

            def total(col: str) -> Decimal:
                return sum((Decimal(str(r[col])) for r in live), Decimal("0"))

            assert kpi.pay_amount == total("pay_amount") == Decimal("1600.00")
            assert kpi.refund_amount == total("refund_amount") == Decimal("90.00")
            assert kpi.brushing_amount == total("brushing_amount") == Decimal("150.00")
            assert kpi.external_spend == total("promo_cost") > 0
            assert kpi.internal_spend == Decimal("132.00")
            assert kpi.actual_sales == Decimal("1360.00")
        finally:
            tenant_id_ctx.reset(tok)


# 店铺日 / 周 / 月（7e R4）
_STORE_LABELS = {"day": "store_daily", "week": "store_weekly", "month": "store_monthly"}
_Granularity = Literal["day", "week", "month"]
_GRANULARITIES: tuple[_Granularity, ...] = ("day", "week", "month")
# 桶行 = 桶内日行之和的列（手填三项与仅退款整桶没录为 None，单列在 _OPTIONAL_SUMS）
_ADDITIVE = (
    "visitors", "pay_amount", "pay_orders", "refund_amount", "brushing_amount", "actual_sales",
    "ad_spend", "internal_spend", "promo_cost", "total_spend", "live_pay_amount",
    "live_ad_spend", "refund_only_days", "day_count",
)  # fmt: skip
_OPTIONAL_SUMS = ("ad_spend_total", "zhitongche_spend", "yinli_spend", "refund_only_amount")
_NON_METRIC = {"date_from", "date_to", "extra"}


def _assert_buckets_are_day_sums(
    buckets: list[StoreReportRow], days: list[StoreReportRow], granularity: str
) -> None:
    """周 / 月行的可加列 = 日接口同期日行之和；行上起止 = 桶内首末日所在的桶 ∩ 区间。"""
    by_bucket: dict[date, list[StoreReportRow]] = {}
    for d in days:
        by_bucket.setdefault(bucket_start(d.date_from, granularity), []).append(d)
    assert [bucket_start(b.date_from, granularity) for b in buckets] == sorted(by_bucket)
    for b in buckets:
        members = by_bucket[bucket_start(b.date_from, granularity)]
        assert b.date_from <= members[0].date_from and members[-1].date_to <= b.date_to
        for col in _ADDITIVE:
            assert getattr(b, col) == sum(getattr(m, col) for m in members), (b.date_from, col)
        for col in _OPTIONAL_SUMS:
            values = [getattr(m, col) for m in members if getattr(m, col) is not None]
            want = sum(values, Decimal("0")) if values else None
            assert getattr(b, col) == want, (b.date_from, col)


async def _store_days(
    session: AsyncSession, tenant: Any, product_factory: Any, pays: dict[date, str]
) -> None:
    """只有千牛日报的日子（一个普通链接，访客 100、支付件数 5、退款 = 支付的 10%）。"""
    style = await product_factory.style(style_code=f"SB{uuid4().hex[:6]}")
    goods = await _goods(session, tenant, style, code=f"G_SB_{uuid4().hex[:5]}", season="春夏")
    pp = await _pp(session, tenant, style, goods)
    session.add_all(
        [_qn(tenant, pp, day, pay, refund=f"{Decimal(pay) / 10:.2f}") for day, pay in pays.items()]
    )
    await session.flush()


def _cross_month_first() -> date:
    """一个不是周一的过去月初：它和前一天在同一个 ISO 周里，周表才有跨月的那周。"""
    first = M_LO
    while first.weekday() == 0:
        first = (first - timedelta(days=1)).replace(day=1)
    return first


class TestStoreReport:
    """店铺日 / 周 / 月三个新接口的 service（7e R4，设计 2.4 与第 8 节）。"""

    @pytest.mark.parametrize("granularity", _GRANULARITIES)
    async def test_summary_equals_live_one_source(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        recorder: _SourceRecorder,
        monkeypatch: pytest.MonkeyPatch,
        granularity: _Granularity,
    ) -> None:
        """① 两路逐字等、每个响应恰一条来源；场景有效性：每个新列至少一行非零。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_store_and_refresh(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            wed, hi = seeded["wed"], seeded["hi"]
            # 引力魔方补在 W+7（第二周），第一周仍只有周三手填了直通车
            await session.execute(
                update(StoreDaily)
                .where(
                    StoreDaily.tenant_id == tenant_a.id, StoreDaily.date == wed + timedelta(days=7)
                )
                .values(yinli_spend=Decimal("7.00"))
            )
            await session.commit()
            label = _STORE_LABELS[granularity]
            svc = StoreDailyService(session)
            via_summary = await svc.get_store_report(tenant_a.id, (wed, hi), granularity)
            assert recorder.calls == [(label, "summary")]

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await svc.get_store_report(tenant_a.id, (wed, hi), granularity)
            assert recorder.calls == [(label, "summary"), (label, "live")]
            assert _dump(via_summary) == _dump(via_live)

            # 刷新后才手填的 W+9 也在（汇总路径并了 store_daily）
            assert via_summary.items[-1].date_to >= wed + timedelta(days=9)
            metrics = set(StoreReportRow.model_fields) - _NON_METRIC
            if granularity == "day":
                assert [i.date_from for i in via_summary.items] == [
                    wed + timedelta(days=n) for n in _STORE_DAYS
                ]
                # 站外推广只落在 W+3（当天实际销售额 0），日行上站外占比都是 None；周 / 月行上有
                metrics.discard("external_ratio")
            dead = [
                col
                for col in sorted(metrics)
                if not any(getattr(i, col) not in (None, 0) for i in via_summary.items)
            ]
            assert dead == [], f"这些列全是 0 / None，场景失效：{dead}"
        finally:
            tenant_id_ctx.reset(tok)

    @pytest.mark.parametrize("granularity", ["week", "month"])
    async def test_wednesday_manual_refund_only_and_day_sums(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        recorder: _SourceRecorder,
        granularity: _Granularity,
    ) -> None:
        """② 周三手填直通车、同周另一天只有万相台：逐天套默 13；③ 仅退款只录部分日子；
        ④ 桶行可加列 = 日接口同期之和。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_store_and_refresh(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            wed, hi = seeded["wed"], seeded["hi"]
            svc = StoreDailyService(session)
            report = await svc.get_store_report(tenant_a.id, (wed, hi), granularity)
            days = await svc.get_store_report(tenant_a.id, (wed, hi), "day")
            assert recorder.calls == [
                (_STORE_LABELS[granularity], "summary"),
                ("store_daily", "summary"),
            ]
            _assert_buckets_are_day_sums(report.items, days.items, granularity)

            first = report.items[0]
            assert first.date_from == wed, "首行起点应是区间起点（周三），不是桶首日"
            want = {
                # W 手填直通车 40（当天万相台 0）+ W+2 只有万相台 60 + 20 → 120；
                # 先分桶再判手填会算成 40
                "internal_spend": "120.00",
                "zhitongche_spend": "40.00",
                "ad_spend_total": None,
                "yinli_spend": None,
                "ad_spend": "80.00",
                "pay_amount": "1600.00",
                "actual_sales": "1360.00",  # 1600 − 退款 90 − 刷单 150
            }
            got = _dump(first)
            if granularity == "week":
                assert first.date_to == wed + timedelta(days=4)  # 周日
                # 仅退款只录了周三：x / y = 1 / 5，率 = 10 ÷ 周三支付 1200（不是 ÷ 全周 1600）
                want |= {
                    "refund_only_amount": "10.00",
                    "refund_only_days": 1,
                    "day_count": 5,
                    "refund_only_rate": "0.0083",
                }
                second = report.items[1]
                # W+7 录了仅退款 5、当天支付 0：分母 0 → None；W+9 没录
                assert (second.refund_only_days, second.day_count) == (1, 2)
                assert second.refund_only_amount == Decimal("5.00")
                assert second.refund_only_rate is None
            else:
                assert report.items == [first]
                # 整段：W 与 W+7 录了，率 = 15 ÷ (1200 + 0)
                want |= {
                    "internal_spend": "132.00",  # 再加 W+9 手填全站推 12
                    "ad_spend_total": "12.00",
                    "refund_only_amount": "15.00",
                    "refund_only_days": 2,
                    "day_count": 7,
                    "refund_only_rate": "0.0125",
                }
            assert {k: got[k] for k in want} == want
            assert (report.recorded_days, report.total_days) == (2, 7)
            assert (days.recorded_days, days.total_days) == (2, 7)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_cross_month_week_and_month_split(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        recorder: _SourceRecorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """⑤ 区间跨月：周表跨月那周一行；月表在月界切两行；行上起止 = 桶 ∩ 区间。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            first = _cross_month_first()
            lo, hi = first - timedelta(days=3), first + timedelta(days=3)
            pays = {first - timedelta(days=1): "100.00", first: "200.00"}
            pays[first + timedelta(days=1)] = "300.00"
            pays.setdefault(M_LO, "50.00")
            await _store_days(session, tenant_a, product_factory, pays)
            await session.commit()
            await _refresh(session, tenant_a, lo, hi)
            svc = StoreDailyService(session)

            weeks = await svc.get_store_report(tenant_a.id, (lo, hi), "week")
            months = await svc.get_store_report(tenant_a.id, (lo, hi), "month")
            assert recorder.calls == [("store_weekly", "summary"), ("store_monthly", "summary")]
            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            assert _dump(weeks) == _dump(await svc.get_store_report(tenant_a.id, (lo, hi), "week"))
            assert _dump(months) == _dump(
                await svc.get_store_report(tenant_a.id, (lo, hi), "month")
            )

            cross = next(w for w in weeks.items if w.date_from <= first <= w.date_to)
            assert cross.date_from <= first - timedelta(days=1), "跨月那周被月界切开了"
            want_week = sum(
                Decimal(p)
                for d, p in pays.items()
                if lo <= d <= hi and bucket_start(d, "week") == bucket_start(first, "week")
            )
            assert cross.pay_amount == want_week
            assert len(weeks.items) == len({bucket_start(d, "week") for d in pays if lo <= d <= hi})

            assert [(m.date_from, m.date_to, m.pay_amount) for m in months.items] == [
                (lo, first - timedelta(days=1), Decimal("100.00")),
                (first, hi, Decimal("500.00")),
            ]
            # 周表区间从 M_LO 起：首行起点 = M_LO（M_LO 不是周一时也不回退到周一）
            month_weeks = await svc.get_store_report(tenant_a.id, (M_LO, M_HI), "week")
            assert month_weeks.items[0].date_from == M_LO
        finally:
            tenant_id_ctx.reset(tok)

    @pytest.mark.parametrize("granularity", _GRANULARITIES)
    async def test_partial_coverage_whole_range_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        recorder: _SourceRecorder,
        granularity: _Granularity,
    ) -> None:
        """⑥ 区间里有一天没覆盖：整段实时，只记一次 live（不按桶各自选来源）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed_store_and_refresh(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            svc = StoreDailyService(session)
            report = await svc.get_store_report(
                tenant_a.id,
                (M_LO - timedelta(days=1), seeded["hi"]),
                granularity,
            )
            assert recorder.calls == [(_STORE_LABELS[granularity], "live")]
            assert report.total_days == len(_STORE_DAYS)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_incremental_refresh_without_full_backfill(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        recorder: _SourceRecorder,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """⑦ 不做前置全量，直接刷 [M_LO+15, M_HI]：周 / 月行 = 桶内日行之和，两路逐字等。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            lo = M_LO + timedelta(days=15)
            pays = {lo: "100.00", lo + timedelta(days=1): "250.00", lo + timedelta(days=8): "70.00"}
            await _store_days(session, tenant_a, product_factory, pays)
            session.add_all(
                [
                    StoreDaily(
                        tenant_id=tenant_a.id,
                        date=lo + timedelta(days=1),
                        zhitongche_spend=Decimal("30.00"),
                        refund_only_amount=Decimal("5.00"),
                    ),
                    StoreDaily(tenant_id=tenant_a.id, date=lo, refund_only_amount=Decimal("0")),
                ]
            )
            await session.commit()
            await _refresh(session, tenant_a, lo, M_HI)
            svc = StoreDailyService(session)

            grans: tuple[_Granularity, ...] = ("week", "month")
            days = await svc.get_store_report(tenant_a.id, (lo, M_HI), "day")
            assert [d.date_from for d in days.items] == sorted(pays)
            via_summary = {g: await svc.get_store_report(tenant_a.id, (lo, M_HI), g) for g in grans}
            assert recorder.calls == [
                ("store_daily", "summary"),
                ("store_weekly", "summary"),
                ("store_monthly", "summary"),
            ]
            for g in grans:
                _assert_buckets_are_day_sums(via_summary[g].items, days.items, g)

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            for g in grans:
                via_live = await svc.get_store_report(tenant_a.id, (lo, M_HI), g)
                assert recorder.calls[-1] == (_STORE_LABELS[g], "live")
                assert _dump(via_summary[g]) == _dump(via_live)

            (month,) = via_summary["month"].items
            assert (month.pay_amount, month.internal_spend) == (Decimal("420.00"), Decimal("30.00"))
            # 手填 0 也算录了：x / y = 2 / 3，率 = 5 ÷ (100 + 250)
            assert (month.refund_only_days, month.day_count) == (2, 3)
            assert month.refund_only_rate == Decimal("0.0143")
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
