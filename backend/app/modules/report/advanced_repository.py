"""U14 报表进阶聚合仓储（工作进度/爆款约篇/店铺/投产）。

只读聚合（text() 原生 SQL）+ 显式 WHERE tenant_id（RLS 之外防御层）。
比率指标由 service 层 safe_div 后处理（分母 0→null 语义统一）。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.urge_calculator import URGE_STATUS_SQL_EXPR
from app.services.metric.publish_progress import like_sum_expr
from app.services.metric.work_progress import HIT_STAT_THRESHOLD

_URGE = URGE_STATUS_SQL_EXPR
_LIKE = like_sum_expr("p.like_count")
_URGE_DAYS = 10
_IMPORTANT_DAYS = 3


class WorkProgressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def aggregate_by_pr(
        self, *, tenant_id: UUID, date_from: date, date_to: date, today: date
    ) -> list[Mapping[str, Any]]:
        sql = text(
            f"""
            SELECT
              p.pr_id AS pr_id,
              COALESCE(u.display_name, u.username, '未分配') AS pr_name,
              COUNT(*) AS quote_count,
              COUNT(*) FILTER (WHERE ({_URGE}) = '档期内') AS in_schedule_count,
              COUNT(*) FILTER (WHERE ({_URGE}) = '催发') AS urge_count,
              COUNT(*) FILTER (WHERE ({_URGE}) = '重要催发') AS important_urge_count,
              COUNT(*) FILTER (WHERE ({_URGE}) = '超时') AS overdue_count,
              COUNT(*) FILTER (WHERE p.publish_status='已发布') AS publish_count,
              COUNT(*) FILTER (WHERE p.publish_status='已发布' AND p.like_count IS NOT NULL)
                AS info_complete_count,
              COUNT(*) FILTER (WHERE p.publish_status='已取消') AS cancel_count,
              COUNT(*) FILTER (WHERE p.recall_status IN ('召回中','召回成功','召回失败'))
                AS recall_due_count,
              COUNT(*) FILTER (WHERE p.recall_status='召回成功') AS recall_success_count,
              COUNT(*) FILTER (WHERE p.publish_status='已发布'
                               AND p.like_count >= :hit_stat) AS hit_count,
              {_LIKE} AS like_count,
              COALESCE(SUM(p.cost_snapshot), 0) AS cost
            FROM promotion p
            LEFT JOIN "user" u ON u.id = p.pr_id
            WHERE p.tenant_id = :tenant_id AND p.is_active = true
              AND p.cooperation_date BETWEEN :date_from AND :date_to
            GROUP BY p.pr_id, u.display_name, u.username
            ORDER BY quote_count DESC
            """
        )
        params = {
            "tenant_id": tenant_id, "date_from": date_from, "date_to": date_to,
            "today": today, "urge_days": _URGE_DAYS,
            "important_days": _IMPORTANT_DAYS, "hit_stat": HIT_STAT_THRESHOLD,
        }
        return list((await self._s.execute(sql, params)).mappings().all())


class TargetPlanningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def list_with_actuals(
        self, *, tenant_id: UUID, month: str
    ) -> list[Mapping[str, Any]]:
        sql = text(
            """
            SELECT
              t.id AS id, t.pr_id AS pr_id, t.style_id AS style_id,
              t.period_month AS period_month, t.min_target AS min_target,
              COALESCE(u.display_name, u.username, '未分配') AS pr_name,
              s.style_code AS style_code, s.style_name AS style_name,
              COALESCE(act.actual, 0) AS actual_count
            FROM target_planning t
            JOIN style s ON s.id = t.style_id
            LEFT JOIN "user" u ON u.id = t.pr_id
            LEFT JOIN (
              SELECT pr_id, style_id, COUNT(*) AS actual FROM promotion
              WHERE tenant_id = :tenant_id AND is_active = true
                AND to_char(cooperation_date, 'YYYY-MM') = :month
              GROUP BY pr_id, style_id
            ) act ON act.pr_id = t.pr_id AND act.style_id = t.style_id
            WHERE t.tenant_id = :tenant_id AND t.period_month = :month
            ORDER BY s.style_code
            """
        )
        return list(
            (
                await self._s.execute(sql, {"tenant_id": tenant_id, "month": month})
            ).mappings().all()
        )


class StoreDailyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def aggregate(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        sql = text(
            """
            SELECT
              q.date AS date,
              COALESCE(SUM(q.visitors), 0) AS visitors,
              COALESCE(SUM(q.pay_amount), 0) AS pay_amount,
              COALESCE(SUM(q.pay_orders), 0) AS pay_orders,
              MAX(sd.ad_spend_total) AS ad_spend_total,
              MAX(sd.zhitongche_spend) AS zhitongche_spend,
              MAX(sd.yinli_spend) AS yinli_spend
            FROM qianniu_daily q
            LEFT JOIN store_daily sd
              ON sd.date = q.date AND sd.tenant_id = q.tenant_id
            WHERE q.tenant_id = :tenant_id AND q.date BETWEEN :date_from AND :date_to
            GROUP BY q.date
            ORDER BY q.date
            """
        )
        return list(
            (
                await self._s.execute(
                    sql,
                    {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
                )
            ).mappings().all()
        )


class ProductionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def aggregate_by_style(
        self, *, tenant_id: UUID, date_from: date, date_to: date,
        exclude_brushing: bool = False, season: str | None = None,
    ) -> list[Mapping[str, Any]]:
        # ad_daily / promotion 各自子查询预聚合为 style 维度，避免与 qianniu 多行笛卡尔积
        # U16：exclude_brushing=true 时 pay_amount 减去刷单金额（真实 ROI）
        brushing_sub = (
            """
            - COALESCE((
                SELECT SUM(oa.amount) FROM order_adjustment oa
                WHERE oa.tenant_id = :tenant_id AND oa.style_id = s.id
                  AND oa.order_type = '刷单' AND oa.exclude_from_roi = true
                  AND oa.order_date BETWEEN :date_from AND :date_to
              ), 0)
            """
            if exclude_brushing
            else ""
        )
        season_clause = "AND s.season = :season" if season else ""
        sql = text(
            f"""
            SELECT
              s.id AS style_id, s.style_code AS style_code, s.style_name AS style_name,
              (COALESCE(SUM(q.pay_amount), 0){brushing_sub}) AS pay_amount,
              COALESCE(SUM((q.extra->>'refund_amount')::numeric), 0) AS refund_amount,
              COALESCE(SUM((q.extra->>'add_cart_count')::int), 0) AS add_cart_count,
              COALESCE(MAX(promo.promo_cost), 0) AS promo_cost,
              COALESCE(MAX(ad.ad_spend), 0) AS ad_spend
            FROM style s
            LEFT JOIN platform_product pp ON pp.style_id = s.id
            LEFT JOIN qianniu_daily q
              ON (q.platform_product_id = pp.id
                  OR q.platform_id_snapshot = pp.platform_id
                  OR (s.qianniu_product_id IS NOT NULL
                      AND q.platform_id_snapshot = s.qianniu_product_id))
              AND q.tenant_id = s.tenant_id
              AND q.date BETWEEN :date_from AND :date_to
            LEFT JOIN (
              SELECT pp2.style_id, SUM(a.cost) AS ad_spend FROM ad_daily a
              JOIN platform_product pp2
                ON (pp2.id = a.platform_product_id
                    OR pp2.platform_id = a.platform_id_snapshot)
                AND pp2.tenant_id = a.tenant_id
              WHERE a.date BETWEEN :date_from AND :date_to
              GROUP BY pp2.style_id
            ) ad ON ad.style_id = s.id
            LEFT JOIN (
              SELECT style_id, SUM(quote_amount) AS promo_cost FROM promotion
              WHERE cooperation_date BETWEEN :date_from AND :date_to AND is_active = true
              GROUP BY style_id
            ) promo ON promo.style_id = s.id
            WHERE s.tenant_id = :tenant_id AND s.is_deleted = false
              {season_clause}
            GROUP BY s.id, s.style_code, s.style_name
            HAVING COALESCE(SUM(q.pay_amount), 0) > 0
                OR COALESCE(MAX(promo.promo_cost), 0) > 0
                OR COALESCE(MAX(ad.ad_spend), 0) > 0
            ORDER BY pay_amount DESC
            """
        )
        params = {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to}
        if season:
            params["season"] = season
        return list(
            (await self._s.execute(sql, params)).mappings().all()
        )

    async def daily_trend(
        self, *, tenant_id: UUID, style_id: UUID, date_from: date, date_to: date
    ) -> list[dict[str, Any]]:
        """单款按日的 支付金额(千牛) + 推广花费(站内ad + 站外promo)，用于投产趋势折线图。"""
        params = {"sid": style_id, "t": tenant_id, "df": date_from, "dt": date_to}
        pay_sql = text(
            """
            SELECT q.date AS d, COALESCE(SUM(q.pay_amount), 0) AS v
            FROM style s
            LEFT JOIN platform_product pp ON pp.style_id = s.id
            JOIN qianniu_daily q
              ON (q.platform_product_id = pp.id
                  OR q.platform_id_snapshot = pp.platform_id
                  OR (s.qianniu_product_id IS NOT NULL
                      AND q.platform_id_snapshot = s.qianniu_product_id))
              AND q.tenant_id = s.tenant_id
            WHERE s.id = :sid AND s.tenant_id = :t AND q.date BETWEEN :df AND :dt
            GROUP BY q.date
            """
        )
        ad_sql = text(
            """
            SELECT a.date AS d, COALESCE(SUM(a.cost), 0) AS v
            FROM ad_daily a
            JOIN platform_product pp
              ON (pp.id = a.platform_product_id OR pp.platform_id = a.platform_id_snapshot)
              AND pp.tenant_id = a.tenant_id
            WHERE pp.style_id = :sid AND a.tenant_id = :t AND a.date BETWEEN :df AND :dt
            GROUP BY a.date
            """
        )
        promo_sql = text(
            """
            SELECT cooperation_date AS d, COALESCE(SUM(quote_amount), 0) AS v
            FROM promotion
            WHERE style_id = :sid AND tenant_id = :t
              AND cooperation_date BETWEEN :df AND :dt AND is_active = true
            GROUP BY cooperation_date
            """
        )
        pay: dict[Any, Any] = {
            r["d"]: r["v"]
            for r in (await self._s.execute(pay_sql, params)).mappings()
        }
        spend: dict[Any, Any] = {}
        for r in (await self._s.execute(ad_sql, params)).mappings():
            spend[r["d"]] = spend.get(r["d"], 0) + r["v"]
        for r in (await self._s.execute(promo_sql, params)).mappings():
            spend[r["d"]] = spend.get(r["d"], 0) + r["v"]
        dates = sorted(set(pay) | set(spend))
        return [
            {
                "date": d.isoformat() if hasattr(d, "isoformat") else str(d),
                "pay_amount": float(pay.get(d, 0) or 0),
                "spend": float(spend.get(d, 0) or 0),
            }
            for d in dates
        ]

    async def daily_trend_by_style(
        self, *, tenant_id: UUID, style_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        """单款按日趋势：每天的千牛支付金额 + 站内投放花费（用于折线图）。

        千牛按 platform_product 映射或款式 qianniu_product_id 直连归集；
        站内花费按 platform_product 归集到款式。
        """
        sql = text(
            """
            WITH pay AS (
              SELECT q.date AS d, SUM(q.pay_amount) AS pay_amount
              FROM style s
              LEFT JOIN platform_product pp ON pp.style_id = s.id
              JOIN qianniu_daily q
                ON (q.platform_product_id = pp.id
                    OR q.platform_id_snapshot = pp.platform_id
                    OR (s.qianniu_product_id IS NOT NULL
                        AND q.platform_id_snapshot = s.qianniu_product_id))
                AND q.tenant_id = s.tenant_id
                AND q.date BETWEEN :date_from AND :date_to
              WHERE s.id = :style_id AND s.tenant_id = :tenant_id
              GROUP BY q.date
            ),
            spend AS (
              SELECT a.date AS d, SUM(a.cost) AS ad_spend
              FROM ad_daily a
              JOIN platform_product pp2
                ON (pp2.id = a.platform_product_id
                    OR pp2.platform_id = a.platform_id_snapshot)
                AND pp2.tenant_id = a.tenant_id
              WHERE pp2.style_id = :style_id AND a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
              GROUP BY a.date
            )
            SELECT COALESCE(pay.d, spend.d) AS date,
                   COALESCE(pay.pay_amount, 0) AS pay_amount,
                   COALESCE(spend.ad_spend, 0) AS ad_spend
            FROM pay FULL OUTER JOIN spend ON pay.d = spend.d
            ORDER BY 1
            """
        )
        return list(
            (
                await self._s.execute(
                    sql,
                    {
                        "tenant_id": tenant_id, "style_id": style_id,
                        "date_from": date_from, "date_to": date_to,
                    },
                )
            ).mappings().all()
        )

    async def fetch_extra_by_style(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        """拉取千牛/站内导入明细的 (style_id, extra) 用于按款式汇总 JSONB 数值列。

        通过 platform_product.platform_id = *_daily.platform_id_snapshot 归集到款式，
        兼容导入数据 platform_product_id 为 NULL 的场景。
        """
        sql = text(
            """
            SELECT pp.style_id AS style_id, q.extra AS extra, 'qianniu' AS src
            FROM qianniu_daily q
            JOIN platform_product pp
              ON pp.tenant_id = q.tenant_id
              AND (pp.id = q.platform_product_id
                   OR pp.platform_id = q.platform_id_snapshot)
            WHERE q.tenant_id = :tenant_id
              AND q.date BETWEEN :date_from AND :date_to
              AND q.extra IS NOT NULL
            UNION ALL
            SELECT s.id AS style_id, q.extra AS extra, 'qianniu' AS src
            FROM qianniu_daily q
            JOIN style s
              ON s.tenant_id = q.tenant_id
              AND s.qianniu_product_id IS NOT NULL
              AND s.qianniu_product_id = q.platform_id_snapshot
            WHERE q.tenant_id = :tenant_id
              AND q.date BETWEEN :date_from AND :date_to
              AND q.extra IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM platform_product pp
                WHERE pp.tenant_id = q.tenant_id
                  AND (pp.id = q.platform_product_id
                       OR pp.platform_id = q.platform_id_snapshot)
              )
            UNION ALL
            SELECT pp.style_id AS style_id, a.extra AS extra, 'ad' AS src
            FROM ad_daily a
            JOIN platform_product pp
              ON pp.tenant_id = a.tenant_id
              AND (pp.id = a.platform_product_id
                   OR pp.platform_id = a.platform_id_snapshot)
            WHERE a.tenant_id = :tenant_id
              AND a.date BETWEEN :date_from AND :date_to
              AND a.extra IS NOT NULL
            """
        )
        return list(
            (
                await self._s.execute(
                    sql,
                    {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
                )
            ).mappings().all()
        )


async def style_exists(session: AsyncSession, tenant_id: UUID, style_id: UUID) -> bool:
    sql = text("SELECT 1 FROM style WHERE id = :sid AND tenant_id = :tid LIMIT 1")
    return (
        await session.execute(sql, {"sid": style_id, "tid": tenant_id})
    ).scalar_one_or_none() is not None


__all__ = [
    "ProductionRepository",
    "StoreDailyRepository",
    "TargetPlanningRepository",
    "WorkProgressRepository",
    "style_exists",
]
