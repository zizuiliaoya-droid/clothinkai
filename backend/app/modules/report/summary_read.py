"""报表从汇总表读取（PRD V1.4 模块三，方案 2）。

## 什么时候读汇总表

**请求区间内每一天都被刷新过**才读汇总表，否则整段走实时聚合。依据是 053 的
``report_summary_coverage``：汇总表里「某天没有行」可能是那天没数据，也可能是从没刷过，
只有覆盖记录能区分。

不做「刷过的日子读汇总 + 没刷过的读实时」再拼起来：投产报表按商品聚合、有 HAVING，
两段各自聚合再合并要把 HAVING 挪到合并之后重算，等于又写一遍聚合逻辑。整段二选一，
两条路径各自完整、各自可测。

典型效果：
- 最近 7 / 30 天、本月：每小时刷新覆盖 → 汇总表
- 上线前的历史、跨进未来的区间：没覆盖 → 实时（数字永远是对的，只是慢一点）
- 工作进度的「当月」：查的是整月，月底那些还没到的日子刷新不到 → 实时

## 两条路径必须逐分相等

这里每个读取方法都对应一个实时方法，输出列、排序完全一致，见
``tests/integration/test_summary_read_equivalence.py``。共用的 SQL 片段（商品信息列、
季节类目筛选、日期分桶）从 ``advanced_repository`` 导入，不在这里另写。

汇总表里存的是「含刷单」的原值 + 单独一列刷单额（052 docstring），剔刷单口径在这里
减出来。比率一律不读（汇总表也不存），由 service 用 ``safe_div`` 从读出的分子分母算。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import as_mappings
from app.core.metrics import report_summary_reads_total
from app.modules.promotion.urge_calculator import DEFAULT_TENANT_TZ
from app.modules.report.advanced_repository import (
    GOODS_META_COLUMNS,
    GOODS_META_GROUP_BY,
    bucket_expr,
    goods_filter_clauses,
)

Source = Literal["summary", "live"]


@dataclass(frozen=True)
class ReportFreshness:
    """一个区间的数据从哪来、截至什么时候。

    ``data_as_of`` 取区间内**最旧**的那次刷新：页面上的数字「至少新到这个时间」。
    走实时时为 None —— 数字就是此刻的。
    """

    date_from: date
    date_to: date
    source: Source
    data_as_of: datetime | None


_COVERAGE_SQL = text(
    """
    SELECT count(*) AS n, min(refreshed_at) AS oldest
    FROM report_summary_coverage
    WHERE tenant_id = :tenant_id AND stat_date BETWEEN :date_from AND :date_to
    """
)

_COVERED_DATES_SQL = text(
    """
    SELECT stat_date FROM report_summary_coverage
    WHERE tenant_id = :tenant_id AND stat_date BETWEEN :date_from AND :date_to
    ORDER BY stat_date
    """
)

# 「催发漂移」：窗口外已覆盖、而催发分类自上次刷新以来可能已经变了的日子。
#
# 档期内 / 催发 / 重要催发取决于「今天」：刷新那一刻排期还没到的未发布单，过几天分类
# 就变了（最终变成超时），而窗口外的日子不会再被每小时刷新 —— 工作进度读汇总表时会一直
# 停在旧分类上。
#
# 判据：刷新那天（租户时区）排期还没过的未发布单 —— 当时不是「超时」，之后会变。
# 已经超时的（排期 < 刷新那天）分类不会再变，已发布 / 已取消也是终态，都不用管。
# 某天被补刷后 refreshed_at 前移，排期已过的单随之落到「超时」，这一天自然退出名单。
_URGE_DRIFT_SQL = text(
    """
    SELECT DISTINCT c.stat_date
    FROM report_summary_coverage c
    JOIN promotion p
      ON p.tenant_id = c.tenant_id AND p.cooperation_date = c.stat_date
    WHERE c.tenant_id = :tenant_id
      AND c.stat_date < :before
      AND p.is_active = true
      AND p.publish_status IN ('未发布', '异常')
      AND p.scheduled_publish_date IS NOT NULL
      AND p.scheduled_publish_date >= CAST(timezone(CAST(:tz AS text), c.refreshed_at) AS date)
    ORDER BY c.stat_date
    """
)

# 工作进度 14 个计数（与 aggregate_by_pr 的输出一一对应）
_PR_SUM_COLUMNS = (
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


class SummaryReadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ------------------------------------------------------------------ #
    # 覆盖判断
    # ------------------------------------------------------------------ #
    async def freshness(self, tenant_id: UUID, date_from: date, date_to: date) -> ReportFreshness:
        """区间是否被完整覆盖；读取开关关闭时一律走实时。"""
        if not settings.REPORT_SUMMARY_READS_ENABLED or date_from > date_to:
            return ReportFreshness(date_from, date_to, "live", None)
        row = (
            await self._s.execute(
                _COVERAGE_SQL,
                {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
            )
        ).one()
        days = (date_to - date_from).days + 1
        if int(row.n) == days:
            return ReportFreshness(date_from, date_to, "summary", row.oldest)
        return ReportFreshness(date_from, date_to, "live", None)

    async def covered_dates(self, tenant_id: UUID, date_from: date, date_to: date) -> list[date]:
        rows = await self._s.execute(
            _COVERED_DATES_SQL,
            {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
        )
        return list(rows.scalars().all())

    async def urge_drift_dates(self, tenant_id: UUID, *, before: date) -> list[date]:
        """``before`` 之前、已覆盖、催发分类可能已过时的日子（见 ``_URGE_DRIFT_SQL``）。"""
        rows = await self._s.execute(
            _URGE_DRIFT_SQL,
            {"tenant_id": tenant_id, "before": before, "tz": DEFAULT_TENANT_TZ.key},
        )
        return list(rows.scalars().all())

    # ------------------------------------------------------------------ #
    # 投产报表主表 ⇄ ProductionRepository.aggregate_by_goods
    # ------------------------------------------------------------------ #
    async def production_by_goods(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        exclude_brushing: bool,
        seasons: Sequence[str] | None = None,
        categories: Sequence[str] | None = None,
    ) -> list[Mapping[str, Any]]:
        filter_sql, filter_params = goods_filter_clauses(seasons, categories)
        sql = text(
            f"""
            SELECT
              {GOODS_META_COLUMNS},
              -- 汇总表存含刷单原值，剔刷单口径在这里减（052 docstring）
              COALESCE(SUM(s.pay_amount), 0)
                - CASE WHEN :exclude_brushing
                       THEN COALESCE(SUM(s.brushing_amount), 0)
                       ELSE 0 END AS pay_amount,
              COALESCE(SUM(s.refund_amount), 0) AS refund_amount,
              COALESCE(SUM(s.add_cart_count), 0) AS add_cart_count,
              COALESCE(SUM(s.promo_cost), 0) AS promo_cost,
              COALESCE(SUM(s.ad_spend), 0) AS ad_spend
            FROM goods_main g
            JOIN product_roi_summary s
              ON s.goods_main_id = g.id
             AND s.tenant_id = g.tenant_id
             AND s.stat_date BETWEEN :date_from AND :date_to
            WHERE g.tenant_id = :tenant_id AND g.is_deleted = false
              {filter_sql}
            GROUP BY {GOODS_META_GROUP_BY}
            -- 与实时路径同一个 HAVING：看的是含刷单的销售原值，不是扣过刷单的
            HAVING COALESCE(SUM(s.pay_amount), 0) > 0
                OR COALESCE(SUM(s.promo_cost), 0) > 0
                OR COALESCE(SUM(s.ad_spend), 0) > 0
            ORDER BY pay_amount DESC, goods_code
            """
        )
        params = {
            "tenant_id": tenant_id,
            "date_from": date_from,
            "date_to": date_to,
            "exclude_brushing": exclude_brushing,
            **filter_params,
        }
        return as_mappings((await self._s.execute(sql, params)).mappings().all())

    # ------------------------------------------------------------------ #
    # 投产趋势 ⇄ ProductionRepository.daily_trend_by_goods(goods_id=X)
    # ------------------------------------------------------------------ #
    async def production_trend(
        self,
        *,
        tenant_id: UUID,
        goods_id: UUID,
        date_from: date,
        date_to: date,
        granularity: str,
        exclude_brushing: bool,
    ) -> list[Mapping[str, Any]]:
        # 汇总表一行一天，日期桶直接对 stat_date 分 —— 刷新源按各自的业务日期
        # （q.date / a.date / p.cooperation_date / oa.order_date）落到同一天，
        # 再按同一套模板分桶，结果与实时一致
        bucket = bucket_expr(granularity, "s.stat_date")
        sql = text(
            f"""
            WITH b AS (
              SELECT {bucket} AS d,
                     COALESCE(SUM(s.pay_amount), 0)
                       - CASE WHEN :exclude_brushing
                              THEN COALESCE(SUM(s.brushing_amount), 0)
                              ELSE 0 END AS pay_amount,
                     COALESCE(SUM(s.brushing_amount), 0) AS brushing_amount,
                     COALESCE(SUM(s.refund_amount), 0) AS refund_amount,
                     COALESCE(SUM(s.add_cart_count), 0) AS add_cart_count,
                     COALESCE(SUM(s.promo_cost), 0) AS promo_cost,
                     COALESCE(SUM(s.ad_spend), 0) AS ad_spend
              FROM product_roi_summary s
              WHERE s.tenant_id = :tenant_id
                AND s.goods_main_id = :goods_id
                AND s.stat_date BETWEEN :date_from AND :date_to
              GROUP BY 1
            )
            SELECT d AS date, pay_amount, brushing_amount, refund_amount,
                   add_cart_count,
                   pay_amount - refund_amount AS confirmed_amount,
                   promo_cost, ad_spend,
                   promo_cost + ad_spend AS total_spend
            FROM b
            ORDER BY d
            """
        )
        params = {
            "tenant_id": tenant_id,
            "goods_id": goods_id,
            "date_from": date_from,
            "date_to": date_to,
            "exclude_brushing": exclude_brushing,
        }
        return as_mappings((await self._s.execute(sql, params)).mappings().all())

    # ------------------------------------------------------------------ #
    # 工作进度 ⇄ WorkProgressRepository.aggregate_by_pr
    # ------------------------------------------------------------------ #
    async def work_progress_by_pr(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        sums = ",\n".join(f"COALESCE(SUM(s.{c}), 0) AS {c}" for c in _PR_SUM_COLUMNS)
        sql = text(
            f"""
            SELECT
              s.pr_id AS pr_id,
              COALESCE(u.display_name, u.username, '未分配') AS pr_name,
              {sums}
            FROM pr_work_progress_summary s
            LEFT JOIN "user" u ON u.id = s.pr_id
            WHERE s.tenant_id = :tenant_id
              AND s.stat_date BETWEEN :date_from AND :date_to
            GROUP BY s.pr_id, u.display_name, u.username
            ORDER BY quote_count DESC, pr_name, pr_id
            """
        )
        params = {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to}
        return as_mappings((await self._s.execute(sql, params)).mappings().all())

    # ------------------------------------------------------------------ #
    # 店铺数据 ⇄ StoreDailyRepository.aggregate
    # ------------------------------------------------------------------ #
    async def store_daily(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        # store_daily 是人工 override 层（3 个广告消耗），读取时 LEFT JOIN 过去即时生效，
        # 不进汇总表 —— 手填完不用等下一次刷新
        sql = text(
            """
            SELECT
              s.stat_date AS date,
              s.visitors AS visitors,
              s.pay_amount AS pay_amount,
              s.pay_orders AS pay_orders,
              sd.ad_spend_total AS ad_spend_total,
              sd.zhitongche_spend AS zhitongche_spend,
              sd.yinli_spend AS yinli_spend
            FROM shop_daily_summary s
            LEFT JOIN store_daily sd
              ON sd.tenant_id = s.tenant_id AND sd.date = s.stat_date
            WHERE s.tenant_id = :tenant_id
              AND s.stat_date BETWEEN :date_from AND :date_to
            ORDER BY s.stat_date
            """
        )
        params = {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to}
        return as_mappings((await self._s.execute(sql, params)).mappings().all())


def record_source(report: str, source: Source) -> None:
    """记一笔这次读取走了哪条路径（/metrics 上能看出切换后汇总表实际被用了多少）。"""
    report_summary_reads_total.labels(report=report, source=source).inc()


__all__ = ["ReportFreshness", "Source", "SummaryReadRepository", "record_source"]
