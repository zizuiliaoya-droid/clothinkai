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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.report.production_service as production_mod
import app.modules.report.store_daily_service as store_mod
import app.modules.report.work_progress_service as work_mod
from app.core.config import settings
from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import AdDaily, QianniuDaily
from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.promotion.urge_calculator import get_today
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
    session: AsyncSession, tenant: Any, style: Any, goods: GoodsMain, *, platform: str = "千牛"
) -> PlatformProduct:
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform=platform,
        platform_id=f"P{uuid4().hex[:8]}",
        style_id=style.id,
        goods_main_id=goods.id,
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
) -> QianniuDaily:
    return QianniuDaily(
        tenant_id=tenant.id,
        platform_product_id=pp.id,
        platform_id_snapshot=pp.platform_id,
        date=day,
        visitors=100,
        pay_amount=Decimal(pay),
        pay_orders=5,
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
            via_summary = await svc.get_for_month(tenant_a.id, MONTH)
            assert ("work_progress", "summary") in recorder.calls

            monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
            via_live = await svc.get_for_month(tenant_a.id, MONTH)
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

    async def test_current_month_stays_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        recorder: _SourceRecorder,
    ) -> None:
        """当月包含还没到的日子，刷新覆盖不到 → 一定走实时。

        这是刻意的行为（PR 盯着当月看，实时对他们最有用），钉住免得以后被「优化」掉。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _refresh(session, tenant_a, TODAY - timedelta(days=30), TODAY)
            await WorkProgressService(session).get_for_month(tenant_a.id, f"{TODAY:%Y-%m}")
            assert recorder.calls == [("work_progress", "live")]
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
