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

直接「删掉区间内的行，再插本次结果」则一步到位。删与插在同一个事务里，事务外的
读取方在提交前看到旧数据、提交后看到新数据，看不到中间的空表。

删除范围严格限制在刷新区间内 —— 这是增量刷新，区间外的历史汇总一行不动。

## 为什么要加锁

「区间删 + 批量插」**不能并发**。两次刷新区间重叠时（每小时的定时任务撞上页面
手动刷新），READ COMMITTED 下后一个事务的 DELETE 看不到前一个刚插入、尚未提交的
行，于是它的 INSERT 会卡在唯一索引上，等前一个提交后报 ``UniqueViolation``。
5b-1 的初版注释写着「两个请求撞上时最终写出同一份数据，不加锁」—— 那是错的。

锁用 ``pg_try_advisory_xact_lock``：
- **事务级**：随 commit / rollback 自动释放，进程崩了也不会留下死锁
- **try 而不是阻塞**：拿不到就报忙。排队会占住 web worker 或 Celery 槽位
  （生产 worker 只有 2 个并发，还要跑采集与备份）；定时任务跳过这一轮，
  下个小时自然补上
- **租户粒度**：不同租户的刷新互不影响；同租户的区间是否重叠不值得细算

## 覆盖记录

读取切到汇总表之后，「某天没有汇总行」有两种可能：那天确实没数据，或者那天从来
没刷新过。不区分的话，没刷新过的历史区间会静默显示成 0。所以每次刷新在
``report_summary_coverage`` 里给区间内**每一天**记一笔（不论那天有没有数据），
读取侧据此判断能不能用汇总表、不能就回退实时聚合。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Date, cast, delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import (
    ProductionRepository,
    StoreDailyRepository,
    WorkProgressRepository,
)
from app.modules.report.exceptions import SummaryRefreshBusyError
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
    "add_cart_count",
    "promo_cost",
    "ad_spend",
)

# pr_work_progress_summary 落盘的计数列（14 个，与 aggregate_by_pr 的输出一一对应）。
# 档期内 / 催发 / 重要催发 / 超时 4 个由 urge_status 派生、依赖「今天」，存的是
# **刷新时刻**的状态 —— 见 _refresh_pr_progress 与 migration 054 的 docstring。
_PR_COUNTERS = (
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

_SHOP_METRICS = ("visitors", "pay_amount", "pay_orders")

# 租户级刷新锁。键带命名空间前缀，与 import_tasks 按 batch_id 取的锁不会撞
# （两边都是 hashtextextended 到 bigint 键空间，字符串不同即可）。
# CAST 必须写：同一参数类型推断不出来时 asyncpg 会报 IndeterminateDatatype。
_LOCK_SQL = text("SELECT pg_try_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))")


def _lock_key(tenant_id: UUID) -> str:
    """刷新锁的键。单独成函数是为了让测试拿同一个键去占锁，不在测试里再写一遍格式。"""
    return f"report_summary:{tenant_id}"


# 覆盖记录：区间内每一天一行，不论那天有没有数据。
# 用 upsert 而不是删插：覆盖记录不会「变少」，一天刷过就是刷过，只更新时间。
_COVERAGE_SQL = text(
    """
    INSERT INTO report_summary_coverage
      (id, tenant_id, stat_date, refreshed_at, created_at, updated_at)
    SELECT gen_random_uuid(), CAST(:tenant_id AS uuid), d::date,
           CAST(:now AS timestamptz), CAST(:now AS timestamptz), CAST(:now AS timestamptz)
    FROM generate_series(CAST(:lo AS date), CAST(:hi AS date), interval '1 day') AS d
    ON CONFLICT (tenant_id, stat_date) DO UPDATE
      SET refreshed_at = EXCLUDED.refreshed_at, updated_at = EXCLUDED.updated_at
    """
)


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


def _next_bucket(bucket_start: date, period: str) -> date:
    if period == "week":
        return bucket_start + timedelta(days=7)
    # 月：跳到下月 1 号。手写而不用 relativedelta，省一个依赖
    if bucket_start.month == 12:
        return bucket_start.replace(year=bucket_start.year + 1, month=1, day=1)
    return bucket_start.replace(month=bucket_start.month + 1, day=1)


def shop_daily_span(date_from: date, date_to: date) -> tuple[date, date]:
    """店铺日汇总要刷新的区间：把 ``[date_from, date_to]`` 扩到完整的周桶与月桶。

    周/月汇总是从日汇总二次聚合的。如果只刷 ``[date_from, date_to]`` 的日汇总，
    落在区间外、但和区间同属一个周/月的那几天就可能**从来没刷过** —— 周/月那一行
    会缺掉它们。定时任务的窗口是「今天往前 31 天」，下沿几乎总是落在某个月中间，
    所以每次都会撞上：窗口 09-02 起，9 月这一行就漏了 09-01。

    店铺日汇总只有一条查询（实测 0.7ms），扩几天没有成本。

    周和月的分桶互相交叠：09-02 所在的周从 08-31 开始，而 08-31 属于 8 月。所以扩出来
    的几天会碰到区间外的桶（8 月）。那个桶也会被重算（见 ``refresh``），周/月行始终
    等于桶内日汇总之和；但 8 月其余日子的日汇总本次没刷，8 月这一行的新鲜度取决于
    它们上次被刷的时间 —— 与「历史数据只能手动刷新」（PRD 模块三）是同一类，不是新问题。
    """
    lo = min(_bucket_start(date_from, "week"), _bucket_start(date_from, "month"))
    hi = max(
        _next_bucket(_bucket_start(date_to, "week"), "week"),
        _next_bucket(_bucket_start(date_to, "month"), "month"),
    ) - timedelta(days=1)
    return lo, hi


class SummaryRefreshService:
    """把实时聚合的结果固化进 5 张汇总表。

    调用方负责 commit —— 刷新是一个整体，5 张表 + 覆盖记录要么一起更新要么一起回滚，
    中间状态（商品 ROI 刷了、店铺日报没刷）会让两张报表对不上账。commit 同时释放
    刷新锁（事务级 advisory lock）。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._production = ProductionRepository(session)
        self._work = WorkProgressRepository(session)
        self._store = StoreDailyRepository(session)

    async def refresh(self, *, tenant_id: UUID, date_from: date, date_to: date) -> dict[str, int]:
        """刷新 ``[date_from, date_to]`` 区间。返回每张表写入的行数。

        同租户已有刷新在进行中时抛 ``SummaryRefreshBusyError``（见模块 docstring
        「为什么要加锁」）。整次刷新共用一个 ``refreshed_at``。
        """
        if date_from > date_to:
            raise ValueError(f"date_from {date_from} 晚于 date_to {date_to}")

        # 必须是事务里的第一件事：锁要罩住后面所有的删与插
        acquired = (await self._s.execute(_LOCK_SQL, {"key": _lock_key(tenant_id)})).scalar_one()
        if not acquired:
            raise SummaryRefreshBusyError()

        now = datetime.now(UTC)
        shop_lo, shop_hi = shop_daily_span(date_from, date_to)
        counts = {
            "product_roi_summary": await self._refresh_product_roi(
                tenant_id, date_from, date_to, now
            ),
            "pr_work_progress_summary": await self._refresh_pr_progress(
                tenant_id, date_from, date_to, now
            ),
            # 扩到完整周/月桶，周月二次聚合才不会缺天（见 shop_daily_span）
            "shop_daily_summary": await self._refresh_shop_daily(tenant_id, shop_lo, shop_hi, now),
        }
        # 周月从日表二次聚合，必须排在日表之后。
        # 区间传扩展后的 shop_lo / shop_hi 而不是原区间：这样**凡是刚刷过的日汇总
        # 所在的周/月都会重算**，周/月行永远等于桶内日汇总之和 —— 月 → 周 → 日
        # 下钻（PRD 投产模块）各层数字对得上。传原区间的话，扩出来的那几天所在的
        # 桶（例如 08-31 所在的 8 月）不会重算，日汇总新了、月汇总还是旧的。
        for period, key in (("week", "shop_week_summary"), ("month", "shop_month_summary")):
            counts[key] = await self._refresh_shop_bucket(
                tenant_id, shop_lo, shop_hi, now, period=period
            )

        # 覆盖记录只记 [date_from, date_to]：这是三张日表**都**刷过的区间。
        # 店铺日表多刷的那几天不算 —— 读取侧拿覆盖记录判断能不能用汇总表，
        # 宁可保守（回退实时）也不能把只刷了一张表的日子当成完整。
        await self._s.execute(
            _COVERAGE_SQL,
            {"tenant_id": str(tenant_id), "lo": date_from, "hi": date_to, "now": now},
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
        # 催发派生的 4 个计数按**刷新时刻的今天**算，整次刷新共用一个值。
        # 读取侧实时路径同样用 get_today()，所以「刚刷新完」时两条路径逐个相等；
        # 之后随时间推移，汇总表里的催发状态停在上次刷新那一刻 —— 与其他计数
        # 同样最多晚一小时（窗口内）或随历史冻结（窗口外），见 migration 054。
        today = get_today()
        day = date_from
        while day <= date_to:
            # aggregate_by_pr 按区间聚合，没有「按日分组」的形态。与其给它加一个
            # granularity 参数（= 改一条 18 个 FILTER 的 SQL，风险远大于收益），
            # 不如把区间收成一天逐日调用：落盘的 14 个计数全是可加的（每张推广单
            # 只有一个合作日期，按天切开再加回去不重不漏），读取时 SUM 回去就是
            # 任意区间的结果。
            rows = await self._work.aggregate_by_pr(
                tenant_id=tenant_id, date_from=day, date_to=day, today=today
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
        从已经校验过口径的日表再 SUM 一次，天然与日表一致 —— 下钻「月 → 周 → 日」时
        各层数字对得上（PRD 投产模块要求的下钻）。

        桶要完整：只刷 3/10~3/15 却写「3 月」那一行，这行就只含 6 天。所以按桶边界
        重算，而桶里每一天的日汇总由 ``shop_daily_span`` 保证刚刚刷过。
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
                ShopDailySummary.stat_date >= bucket_lo,
                ShopDailySummary.stat_date < _next_bucket(bucket_hi, period),
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
        并发安全靠 ``refresh`` 入口的租户锁，这里不再重复处理。
        """
        await self._s.execute(
            delete(model).where(
                model.tenant_id == tenant_id,
                date_col.between(date_from, date_to),
            )
        )
        if payload:
            await self._s.execute(insert(model), list(payload))


__all__ = ["SummaryRefreshService", "shop_daily_span"]
