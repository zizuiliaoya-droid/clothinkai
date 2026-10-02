"""报表中间汇总表的增量刷新（PRD V1.4 模块三）。

## 为什么不写 INSERT ... SELECT

汇总表最大的风险是**预聚合口径和实时查询口径悄悄分叉** —— 数字对不上，不报错、
不抛异常、没有任何信号。这个项目已经为「同一个值两处定义」付过三次代价
（``legacy_settings`` 与 ``scan_service`` 的催发阈值、``like_count`` 与
``source_extra['点赞数']``、推广列表手写列白名单漏 5 列上线后一直是空的）。

所以这里**没有一行聚合 SQL**，全部调 ``advanced_repository`` 现成的方法：

| 表 | 刷新源 |
|---|---|
| ``product_roi_summary`` | ``ProductionRepository.daily_trend_by_goods(goods_id=None)`` |
| ``pr_work_progress_summary`` | ``WorkProgressRepository.aggregate_by_pr`` 逐日 |
| ``shop_daily_summary`` | ``StoreDailyRepository.aggregate`` |
| ``shop_week_summary`` / ``shop_month_summary`` | ``shop_daily_summary`` 二次聚合 |

代价是 PR 进度要逐日调 N 次查询（生产实测单次 11ms，31 天约 340ms，每小时一次）。
用这点开销换「刷新与实时读永远同一份 SQL」，值。

## 为什么是「区间删 + 批量插」而不是 upsert

纯 upsert 会漏一种情况：源数据**减少**时汇总表留下陈旧行。某天的千牛日报被删掉或
改了货号，那天那个商品的汇总行不会被任何 upsert 覆盖，于是永远停在旧数字上，
区间求和偏高。要补这个漏，upsert 之后还得算出「本次没产出的 key」再删 —— 生产
264 商品 × 31 天是 8000 多个 key，拼进 SQL 会炸。

直接「删掉区间内的行，再插本次结果」则一步到位。这**不会**让读取方看到空表：
删与插在同一个事务里，PostgreSQL 的 MVCC 保证事务外的查询在提交前看到的是旧数据、
提交后看到新数据，中间态不可见。

删除范围严格限制在刷新区间内 —— 这是增量刷新，区间外的历史汇总一行不动。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, cast, delete, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.report.advanced_repository import (
    ProductionRepository,
    StoreDailyRepository,
    WorkProgressRepository,
)
from app.modules.report.summary_models import (
    ProductRoiSummary,
    PrWorkProgressSummary,
    ShopDailySummary,
    ShopMonthSummary,
    ShopWeekSummary,
)

# product_roi_summary 直接落盘的指标列（与 daily_trend_by_goods 的输出同名）。
# confirmed_amount / total_spend / net_roi 不在这里：前两个是加法派生，最后一个是
# 比率。比率落盘会把除零语义固化进数据，那个语义现在由 safe_div 一处决定。
_ROI_METRICS = (
    "pay_amount",
    "brushing_amount",
    "refund_amount",
    "promo_cost",
    "ad_spend",
)

# pr_work_progress_summary 落盘的计数列。
# 档期内 / 催发 / 重要催发 / 超时这 4 个**刻意不存**：它们由 urge_status 派生，
# 依赖「今天」—— 今天算催发的单子明天变超时，按历史日期固化下来永远是错的。
_PR_COUNTERS = (
    "quote_count",
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

_SHOP_METRICS = ("visitors", "pay_amount", "pay_orders")


def _bucket_start(day: date, period: str) -> date:
    """与 ``date_trunc(period, ...)`` 对齐的桶首日。

    周按 ISO（周一开始），和 ``daily_trend_by_goods`` 的 ``date_trunc('week', ...)``
    同一口径 —— 两边分桶方式不一致会让周汇总与周趋势图对不上。
    """
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    raise ValueError(f"Unsupported bucket period: {period}")


class SummaryRefreshService:
    """把实时聚合的结果固化进 5 张汇总表。

    调用方负责 commit —— 刷新是一个整体，5 张表要么一起更新要么一起回滚。
    中间状态（商品 ROI 刷了、店铺日报没刷）会让两张报表对不上账。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._production = ProductionRepository(session)
        self._work = WorkProgressRepository(session)
        self._store = StoreDailyRepository(session)

    async def refresh(self, *, tenant_id: UUID, date_from: date, date_to: date) -> dict[str, int]:
        """刷新 ``[date_from, date_to]`` 区间。返回每张表写入的行数。

        整次刷新共用一个 ``refreshed_at``，方便一眼看出哪些行是同一批产出的。
        """
        if date_from > date_to:
            raise ValueError(f"date_from {date_from} 晚于 date_to {date_to}")
        now = datetime.now(UTC)
        counts = {
            "product_roi_summary": await self._refresh_product_roi(
                tenant_id, date_from, date_to, now
            ),
            "pr_work_progress_summary": await self._refresh_pr_progress(
                tenant_id, date_from, date_to, now
            ),
            "shop_daily_summary": await self._refresh_shop_daily(
                tenant_id, date_from, date_to, now
            ),
        }
        # 周月从日表二次聚合，必须排在日表之后
        for period, key in (("week", "shop_week_summary"), ("month", "shop_month_summary")):
            counts[key] = await self._refresh_shop_bucket(
                tenant_id, date_from, date_to, now, period=period
            )
        return counts

    # ------------------------------------------------------------------ #
    # product_roi_summary
    # ------------------------------------------------------------------ #
    async def _refresh_product_roi(
        self, tenant_id: UUID, date_from: date, date_to: date, now: datetime
    ) -> int:
        # exclude_brushing=False 是刻意的：这样 pay_amount 是**含刷单的原值**，
        # 刷单额由 brushing_amount 单独给出。两个口径都能从一份行还原
        # （剔刷单 = pay_amount - brushing_amount），不必为开关存两份。
        rows = await self._production.daily_trend_by_goods(
            tenant_id=tenant_id,
            goods_id=None,
            date_from=date_from,
            date_to=date_to,
            granularity="day",
            exclude_brushing=False,
        )
        payload = [
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "goods_main_id": r["goods_id"],
                "stat_date": r["date"],
                "refreshed_at": now,
                **{m: r[m] for m in _ROI_METRICS},
            }
            for r in rows
            # goods_id 为 NULL 理论上不该出现（四个子查询都要求商品归属非空），
            # 真出现了也不能写 —— 那一列是 NOT NULL + FK
            if r["goods_id"] is not None
        ]
        await self._replace(
            ProductRoiSummary,
            payload,
            tenant_id=tenant_id,
            date_col=ProductRoiSummary.stat_date,
            date_from=date_from,
            date_to=date_to,
        )
        return len(payload)

    # ------------------------------------------------------------------ #
    # pr_work_progress_summary
    # ------------------------------------------------------------------ #
    async def _refresh_pr_progress(
        self, tenant_id: UUID, date_from: date, date_to: date, now: datetime
    ) -> int:
        payload: list[dict[str, Any]] = []
        day = date_from
        while day <= date_to:
            # aggregate_by_pr 按区间聚合，没有「按日分组」的形态。与其给它加一个
            # granularity 参数（= 改一条 18 个 FILTER 的 SQL，风险远大于收益），
            # 不如把区间收成一天逐日调用：落盘的 10 个计数全是可加的，
            # 读取时 SUM 回去就是任意区间的结果。
            #
            # today 传当天：它只影响 urge_status 派生的 5 个计数，而那 5 个
            # 刻意不落盘，所以传什么都不改变写入结果。
            rows = await self._work.aggregate_by_pr(
                tenant_id=tenant_id, date_from=day, date_to=day, today=day
            )
            payload.extend(
                {
                    "id": uuid4(),
                    "tenant_id": tenant_id,
                    "pr_id": r["pr_id"],
                    "stat_date": day,
                    "refreshed_at": now,
                    **{c: 0 if r[c] is None else r[c] for c in _PR_COUNTERS},
                }
                for r in rows
            )
            day += timedelta(days=1)

        await self._replace(
            PrWorkProgressSummary,
            payload,
            tenant_id=tenant_id,
            date_col=PrWorkProgressSummary.stat_date,
            date_from=date_from,
            date_to=date_to,
        )
        return len(payload)

    # ------------------------------------------------------------------ #
    # shop_daily_summary
    # ------------------------------------------------------------------ #
    async def _refresh_shop_daily(
        self, tenant_id: UUID, date_from: date, date_to: date, now: datetime
    ) -> int:
        rows = await self._store.aggregate(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        # aggregate 还返回 store_daily 的 3 个手填广告消耗，这里**不取** ——
        # 那是人工 override 层，读取时 LEFT JOIN 过去即时生效，
        # 存进汇总表反而要等下一次刷新才看得到自己刚填的值。
        payload = [
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "stat_date": r["date"],
                "refreshed_at": now,
                **{m: 0 if r[m] is None else r[m] for m in _SHOP_METRICS},
            }
            for r in rows
        ]
        await self._replace(
            ShopDailySummary,
            payload,
            tenant_id=tenant_id,
            date_col=ShopDailySummary.stat_date,
            date_from=date_from,
            date_to=date_to,
        )
        return len(payload)

    # ------------------------------------------------------------------ #
    # shop_week_summary / shop_month_summary
    # ------------------------------------------------------------------ #
    async def _refresh_shop_bucket(
        self,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        now: datetime,
        *,
        period: str,
    ) -> int:
        """从 ``shop_daily_summary`` 二次聚合出周/月。

        不另写一条按周/月分桶的聚合 SQL：周月就是日的加总，三个指标都可加，
        从已经校验过口径的日表再 SUM 一次，天然与日表一致。

        **区间会被扩展到完整的桶**。只刷 3/10~3/15 却写「3 月」那一行，这行就只含
        这 6 天 —— 月汇总凭空少掉大半。所以先把区间对齐到桶边界，按完整桶重算。
        """
        if period == "week":
            model: Any = ShopWeekSummary
            bucket_col: Any = ShopWeekSummary.week_start
            bucket_name = "week_start"
        else:
            model = ShopMonthSummary
            bucket_col = ShopMonthSummary.period_month
            bucket_name = "period_month"

        # 区间两端所在的桶 —— 删除与重算都按这个范围，而不是原区间
        bucket_lo = _bucket_start(date_from, period)
        bucket_hi = _bucket_start(date_to, period)

        bucket = cast(func.date_trunc(period, ShopDailySummary.stat_date), Date)
        agg = (
            select(
                bucket.label("bucket"),
                func.coalesce(func.sum(ShopDailySummary.visitors), 0).label("visitors"),
                func.coalesce(func.sum(ShopDailySummary.pay_amount), 0).label("pay_amount"),
                func.coalesce(func.sum(ShopDailySummary.pay_orders), 0).label("pay_orders"),
            )
            .where(
                ShopDailySummary.tenant_id == tenant_id,
                # 桶的完整范围：bucket_hi 那个桶要整个覆盖到，所以上界取下一个桶前一天
                ShopDailySummary.stat_date >= bucket_lo,
                ShopDailySummary.stat_date < self._next_bucket(bucket_hi, period),
            )
            .group_by(bucket)
        )
        rows = (await self._s.execute(agg)).mappings().all()
        payload = [
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                bucket_name: r["bucket"],
                "refreshed_at": now,
                **{m: r[m] for m in _SHOP_METRICS},
            }
            for r in rows
        ]
        await self._replace(
            model,
            payload,
            tenant_id=tenant_id,
            date_col=bucket_col,
            date_from=bucket_lo,
            date_to=bucket_hi,
        )
        return len(payload)

    @staticmethod
    def _next_bucket(bucket_start: date, period: str) -> date:
        if period == "week":
            return bucket_start + timedelta(days=7)
        # 月：跳到下月 1 号。手写而不用 relativedelta，省一个依赖
        if bucket_start.month == 12:
            return bucket_start.replace(year=bucket_start.year + 1, month=1, day=1)
        return bucket_start.replace(month=bucket_start.month + 1, day=1)

    # ------------------------------------------------------------------ #
    # 通用「区间删 + 批量插」
    # ------------------------------------------------------------------ #
    async def _replace(
        self,
        model: Any,
        payload: Sequence[Mapping[str, Any]],
        *,
        tenant_id: UUID,
        date_col: Any,
        date_from: date,
        date_to: date,
    ) -> None:
        """清掉区间内的旧行，写入本次结果。

        先删后插能一并解决「源数据减少后的陈旧行」—— 不需要额外算出哪些 key
        本次没产出（生产那个集合有 8000 多个元素，拼进 SQL 会炸）。

        删与插同事务，读取方看不到中间的空表（MVCC）。
        """
        await self._s.execute(
            delete(model).where(
                model.tenant_id == tenant_id,
                date_col.between(date_from, date_to),
            )
        )
        if payload:
            await self._s.execute(insert(model), list(payload))
