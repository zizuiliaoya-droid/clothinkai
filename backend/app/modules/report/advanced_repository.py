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
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        exclude_brushing: bool = False,
        season: str | None = None,
    ) -> list[Mapping[str, Any]]:
        """按款式聚合；EXISTS/LATERAL 保证每条日报只映射一次。"""
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
              s.main_image_key AS main_image_key,
              (COALESCE(SUM(q.pay_amount), 0){brushing_sub}) AS pay_amount,
              COALESCE(SUM(
                CASE
                  WHEN COALESCE(q.extra->>'refund_amount', '')
                       ~ '^-?[0-9]+([.][0-9]+)?$'
                  THEN (q.extra->>'refund_amount')::numeric
                  ELSE 0
                END
              ), 0) AS refund_amount,
              COALESCE(SUM((q.extra->>'add_cart_count')::int), 0) AS add_cart_count,
              COALESCE(MAX(promo.promo_cost), 0) AS promo_cost,
              COALESCE(MAX(ad.ad_spend), 0) AS ad_spend
            FROM style s
            LEFT JOIN qianniu_daily q
              ON q.tenant_id = s.tenant_id
              AND q.date BETWEEN :date_from AND :date_to
              AND (
                EXISTS (
                  SELECT 1 FROM platform_product mapped_q
                  WHERE mapped_q.tenant_id = q.tenant_id
                    AND mapped_q.style_id = s.id
                    AND mapped_q.platform = '千牛'
                    AND (
                      (q.platform_product_id IS NOT NULL
                       AND mapped_q.id = q.platform_product_id)
                      OR (q.platform_product_id IS NULL
                          AND mapped_q.platform_id = q.platform_id_snapshot)
                    )
                )
                OR (
                  q.platform_product_id IS NULL
                  AND s.qianniu_product_id IS NOT NULL
                  AND q.platform_id_snapshot = s.qianniu_product_id
                  AND NOT EXISTS (
                    SELECT 1 FROM platform_product any_q
                    WHERE any_q.tenant_id = q.tenant_id
                      AND any_q.platform = '千牛'
                      AND any_q.platform_id = q.platform_id_snapshot
                  )
                  AND (
                    SELECT COUNT(*) FROM style legacy_s
                    WHERE legacy_s.tenant_id = q.tenant_id
                      AND legacy_s.is_deleted = false
                      AND legacy_s.qianniu_product_id = q.platform_id_snapshot
                  ) = 1
                )
              )
            LEFT JOIN (
              SELECT mapped_a.style_id, SUM(a.cost) AS ad_spend
              FROM ad_daily a
              JOIN platform_product mapped_a
                ON mapped_a.tenant_id = a.tenant_id
                AND mapped_a.platform = '万相台'
                AND (
                  (a.platform_product_id IS NOT NULL
                   AND mapped_a.id = a.platform_product_id)
                  OR (a.platform_product_id IS NULL
                      AND mapped_a.platform_id = a.platform_id_snapshot)
                )
              WHERE a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
              GROUP BY mapped_a.style_id
            ) ad ON ad.style_id = s.id
            LEFT JOIN (
              SELECT style_id, SUM(quote_amount) AS promo_cost FROM promotion
              WHERE tenant_id = :tenant_id
                AND cooperation_date BETWEEN :date_from AND :date_to
                AND is_active = true AND publish_status = '已发布'
              GROUP BY style_id
            ) promo ON promo.style_id = s.id
            WHERE s.tenant_id = :tenant_id AND s.is_deleted = false
              {season_clause}
            GROUP BY s.id, s.style_code, s.style_name, s.main_image_key
            HAVING COALESCE(SUM(q.pay_amount), 0) > 0
                OR COALESCE(MAX(promo.promo_cost), 0) > 0
                OR COALESCE(MAX(ad.ad_spend), 0) > 0
            ORDER BY pay_amount DESC
            """
        )
        params = {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to}
        if season:
            params["season"] = season
        return list((await self._s.execute(sql, params)).mappings().all())

    async def daily_trend_by_style(
        self,
        *,
        tenant_id: UUID,
        style_id: UUID,
        date_from: date,
        date_to: date,
        granularity: str = "day",
        exclude_brushing: bool = True,
    ) -> list[Mapping[str, Any]]:
        """按日/周/月/年汇总单款投产指标，与投产主表保持相同口径。"""
        bucket_templates = {
            "day": "{column}",
            "week": "date_trunc('week', {column})::date",
            "month": "date_trunc('month', {column})::date",
            "year": "date_trunc('year', {column})::date",
        }
        try:
            template = bucket_templates[granularity]
        except KeyError as exc:
            raise ValueError(f"Unsupported trend granularity: {granularity}") from exc

        sales_bucket = template.format(column="q.date")
        ad_bucket = template.format(column="a.date")
        promo_bucket = template.format(column="p.cooperation_date")
        brushing_bucket = template.format(column="oa.order_date")
        sql = text(
            f"""
            WITH sales AS (
              SELECT {sales_bucket} AS d,
                     COALESCE(SUM(q.pay_amount), 0) AS pay_amount,
                     COALESCE(SUM(
                       CASE
                         WHEN COALESCE(q.extra->>'refund_amount', '')
                              ~ '^-?[0-9]+([.][0-9]+)?$'
                         THEN (q.extra->>'refund_amount')::numeric
                         ELSE 0
                       END
                     ), 0) AS refund_amount
              FROM style s
              JOIN qianniu_daily q
                ON q.tenant_id = s.tenant_id
                AND q.date BETWEEN :date_from AND :date_to
                AND (
                  EXISTS (
                    SELECT 1 FROM platform_product mapped_q
                    WHERE mapped_q.tenant_id = q.tenant_id
                      AND mapped_q.style_id = s.id
                      AND mapped_q.platform = '千牛'
                      AND (
                        (q.platform_product_id IS NOT NULL
                         AND mapped_q.id = q.platform_product_id)
                        OR (q.platform_product_id IS NULL
                            AND mapped_q.platform_id = q.platform_id_snapshot)
                      )
                  )
                  OR (
                    q.platform_product_id IS NULL
                    AND s.qianniu_product_id IS NOT NULL
                    AND q.platform_id_snapshot = s.qianniu_product_id
                    AND NOT EXISTS (
                      SELECT 1 FROM platform_product any_q
                      WHERE any_q.tenant_id = q.tenant_id
                        AND any_q.platform = '千牛'
                        AND any_q.platform_id = q.platform_id_snapshot
                    )
                    AND (
                      SELECT COUNT(*) FROM style legacy_s
                      WHERE legacy_s.tenant_id = q.tenant_id
                        AND legacy_s.is_deleted = false
                        AND legacy_s.qianniu_product_id = q.platform_id_snapshot
                    ) = 1
                  )
                )
              WHERE s.id = :style_id AND s.tenant_id = :tenant_id
              GROUP BY {sales_bucket}
            ),
            brushing AS (
              SELECT {brushing_bucket} AS d,
                     COALESCE(SUM(oa.amount), 0) AS brushing_amount
              FROM order_adjustment oa
              WHERE :exclude_brushing = true
                AND oa.tenant_id = :tenant_id
                AND oa.style_id = :style_id
                AND oa.order_type = '刷单'
                AND oa.exclude_from_roi = true
                AND oa.order_date BETWEEN :date_from AND :date_to
              GROUP BY {brushing_bucket}
            ),
            ads AS (
              SELECT {ad_bucket} AS d,
                     COALESCE(SUM(a.cost), 0) AS ad_spend
              FROM ad_daily a
              WHERE a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
                AND EXISTS (
                  SELECT 1 FROM platform_product mapped_a
                  WHERE mapped_a.tenant_id = a.tenant_id
                    AND mapped_a.style_id = :style_id
                    AND mapped_a.platform = '万相台'
                    AND (
                      (a.platform_product_id IS NOT NULL
                       AND mapped_a.id = a.platform_product_id)
                      OR (a.platform_product_id IS NULL
                          AND mapped_a.platform_id = a.platform_id_snapshot)
                    )
                )
              GROUP BY {ad_bucket}
            ),
            promos AS (
              SELECT {promo_bucket} AS d,
                     COALESCE(SUM(p.quote_amount), 0) AS promo_cost
              FROM promotion p
              WHERE p.tenant_id = :tenant_id
                AND p.style_id = :style_id
                AND p.is_active = true
                AND p.publish_status = '已发布'
                AND p.cooperation_date BETWEEN :date_from AND :date_to
              GROUP BY {promo_bucket}
            ),
            aggregated AS (
              SELECT d,
                     COALESCE(SUM(pay_amount), 0)
                       - COALESCE(SUM(brushing_amount), 0) AS pay_amount,
                     COALESCE(SUM(refund_amount), 0) AS refund_amount,
                     COALESCE(SUM(promo_cost), 0) AS promo_cost,
                     COALESCE(SUM(ad_spend), 0) AS ad_spend
              FROM (
                SELECT d, pay_amount, refund_amount,
                       0::numeric AS brushing_amount,
                       0::numeric AS promo_cost, 0::numeric AS ad_spend
                FROM sales
                UNION ALL
                SELECT d, 0, 0, brushing_amount, 0, 0 FROM brushing
                UNION ALL
                SELECT d, 0, 0, 0, 0, ad_spend FROM ads
                UNION ALL
                SELECT d, 0, 0, 0, promo_cost, 0 FROM promos
              ) source
              GROUP BY d
            ),
            calculated AS (
              SELECT d, pay_amount, refund_amount,
                     pay_amount - refund_amount AS confirmed_amount,
                     promo_cost, ad_spend,
                     promo_cost + ad_spend AS total_spend
              FROM aggregated
            )
            SELECT d AS date, pay_amount, refund_amount, confirmed_amount,
                   promo_cost, ad_spend, total_spend,
                   CASE WHEN total_spend = 0 THEN NULL
                        ELSE ROUND(confirmed_amount / total_spend, 4)
                   END AS net_roi
            FROM calculated
            ORDER BY d
            """
        )
        return list(
            (
                await self._s.execute(
                    sql,
                    {
                        "tenant_id": tenant_id,
                        "style_id": style_id,
                        "date_from": date_from,
                        "date_to": date_to,
                        "exclude_brushing": exclude_brushing,
                    },
                )
            ).mappings().all()
        )

    async def fetch_extra_by_style(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        """按款式拉取千牛/站内 extra，显式 ID 优先并限制平台，避免重复归集。"""
        sql = text(
            """
            SELECT pp.style_id AS style_id, q.extra AS extra, 'qianniu' AS src
            FROM qianniu_daily q
            JOIN platform_product pp
              ON pp.tenant_id = q.tenant_id
              AND pp.platform = '千牛'
              AND (
                (q.platform_product_id IS NOT NULL
                 AND pp.id = q.platform_product_id)
                OR (q.platform_product_id IS NULL
                    AND pp.platform_id = q.platform_id_snapshot)
              )
            WHERE q.tenant_id = :tenant_id
              AND q.date BETWEEN :date_from AND :date_to
              AND q.extra IS NOT NULL
            UNION ALL
            SELECT s.id AS style_id, q.extra AS extra, 'qianniu' AS src
            FROM qianniu_daily q
            JOIN style s
              ON s.tenant_id = q.tenant_id
              AND s.is_deleted = false
              AND s.qianniu_product_id IS NOT NULL
              AND s.qianniu_product_id = q.platform_id_snapshot
            WHERE q.tenant_id = :tenant_id
              AND q.date BETWEEN :date_from AND :date_to
              AND q.extra IS NOT NULL
              AND q.platform_product_id IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM platform_product pp
                WHERE pp.tenant_id = q.tenant_id
                  AND pp.platform = '千牛'
                  AND pp.platform_id = q.platform_id_snapshot
              )
              AND (
                SELECT COUNT(*) FROM style legacy_s
                WHERE legacy_s.tenant_id = q.tenant_id
                  AND legacy_s.is_deleted = false
                  AND legacy_s.qianniu_product_id = q.platform_id_snapshot
              ) = 1
            UNION ALL
            SELECT pp.style_id AS style_id, a.extra AS extra, 'ad' AS src
            FROM ad_daily a
            JOIN platform_product pp
              ON pp.tenant_id = a.tenant_id
              AND pp.platform = '万相台'
              AND (
                (a.platform_product_id IS NOT NULL
                 AND pp.id = a.platform_product_id)
                OR (a.platform_product_id IS NULL
                    AND pp.platform_id = a.platform_id_snapshot)
              )
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


class BiRepository:
    """TASK 15 BI 看板专用聚合，统一合作日期、状态与租户口径。"""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def aggregate_store_summary(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> Mapping[str, Any]:
        sql = text(
            """
            WITH sales AS (
              SELECT COALESCE(SUM(q.pay_amount), 0) AS sales_amount,
                     COALESCE(SUM(
                       CASE
                         WHEN COALESCE(q.extra->>'refund_amount', '')
                              ~ '^[0-9]+([.][0-9]+)?$'
                         THEN (q.extra->>'refund_amount')::numeric
                         ELSE 0
                       END
                     ), 0) AS refund_amount
              FROM qianniu_daily q
              WHERE q.tenant_id = :tenant_id
                AND q.date BETWEEN :date_from AND :date_to
            ), ads AS (
              SELECT COALESCE(SUM(a.cost), 0) AS internal_spend
              FROM ad_daily a
              WHERE a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
            ), promos AS (
              SELECT COALESCE(SUM(p.quote_amount), 0) AS external_spend
              FROM promotion p
              WHERE p.tenant_id = :tenant_id AND p.is_active = true
                AND p.publish_status = '已发布'
                AND p.cooperation_date BETWEEN :date_from AND :date_to
            )
            SELECT sales.sales_amount, sales.refund_amount,
                   ads.internal_spend, promos.external_spend
            FROM sales CROSS JOIN ads CROSS JOIN promos
            """
        )
        return (
            await self._s.execute(
                sql,
                {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
            )
        ).mappings().one()

    async def aggregate_promotion_summary(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> Mapping[str, Any]:
        sql = text(
            """
            SELECT
              COALESCE(SUM(p.quote_amount), 0) AS commission_amount,
              COUNT(*) AS commission_count,
              COALESCE(SUM(p.quote_amount)
                FILTER (WHERE p.publish_status = '已发布'), 0) AS published_spend,
              COUNT(*) FILTER (WHERE p.publish_status = '已发布') AS published_count,
              COALESCE(SUM(p.quote_amount)
                FILTER (WHERE p.publish_status = '未发布'), 0) AS unpublished_spend,
              COUNT(*) FILTER (WHERE p.publish_status = '未发布') AS unpublished_count,
              COALESCE(SUM(p.quote_amount)
                FILTER (WHERE p.publish_status = '已取消'), 0) AS cancelled_amount,
              COUNT(*) FILTER (WHERE p.publish_status = '已取消') AS cancelled_count
            FROM promotion p
            WHERE p.tenant_id = :tenant_id AND p.is_active = true
              AND p.cooperation_date BETWEEN :date_from AND :date_to
            """
        )
        return (
            await self._s.execute(
                sql,
                {"tenant_id": tenant_id, "date_from": date_from, "date_to": date_to},
            )
        ).mappings().one()

    async def aggregate_workload(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        today: date,
    ) -> list[Mapping[str, Any]]:
        sql = text(
            f"""
            WITH work AS (
              SELECT p.pr_id,
                     COALESCE(u.display_name, u.username, '未分配') AS pr_name,
                     COUNT(*) AS quote_count,
                     COUNT(*) FILTER (WHERE p.publish_status = '已发布') AS publish_count,
                     COUNT(*) FILTER (WHERE p.publish_status = '未发布') AS pending_count,
                     COUNT(*) FILTER (WHERE p.publish_status = '已取消') AS cancel_count,
                     COUNT(*) FILTER (WHERE ({_URGE}) = '超时') AS overdue_count
              FROM promotion p
              LEFT JOIN "user" u ON u.id = p.pr_id
              WHERE p.tenant_id = :tenant_id AND p.is_active = true
                AND p.cooperation_date BETWEEN :date_from AND :date_to
              GROUP BY p.pr_id, u.display_name, u.username
            ), targets AS (
              SELECT t.pr_id, COALESCE(SUM(t.min_target), 0) AS target_count
              FROM target_planning t
              WHERE t.tenant_id = :tenant_id
                AND t.period_month BETWEEN
                    to_char(CAST(:date_from AS date), 'YYYY-MM') AND
                    to_char(CAST(:date_to AS date), 'YYYY-MM')
              GROUP BY t.pr_id
            )
            SELECT work.*, COALESCE(targets.target_count, 0) AS target_count
            FROM work
            LEFT JOIN targets ON targets.pr_id = work.pr_id
            ORDER BY work.quote_count DESC, work.pr_name
            """
        )
        params = {
            "tenant_id": tenant_id,
            "date_from": date_from,
            "date_to": date_to,
            "today": today,
            "urge_days": _URGE_DAYS,
            "important_days": _IMPORTANT_DAYS,
        }
        return list((await self._s.execute(sql, params)).mappings().all())

    async def aggregate_trend(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        granularity: str,
    ) -> list[Mapping[str, Any]]:
        bucket_templates = {
            "day": "{column}",
            "week": "date_trunc('week', {column})::date",
            "month": "date_trunc('month', {column})::date",
            "year": "date_trunc('year', {column})::date",
        }
        try:
            template = bucket_templates[granularity]
        except KeyError as exc:
            raise ValueError(f"Unsupported BI granularity: {granularity}") from exc
        q_bucket = template.format(column="q.date")
        a_bucket = template.format(column="a.date")
        p_bucket = template.format(column="p.cooperation_date")
        sql = text(
            f"""
            SELECT d AS date,
                   COALESCE(SUM(sales_amount), 0) AS sales_amount,
                   COALESCE(SUM(refund_amount), 0) AS refund_amount,
                   COALESCE(SUM(internal_spend), 0) AS internal_spend,
                   COALESCE(SUM(external_spend), 0) AS external_spend
            FROM (
              SELECT {q_bucket} AS d,
                     COALESCE(SUM(q.pay_amount), 0) AS sales_amount,
                     COALESCE(SUM(
                       CASE
                         WHEN COALESCE(q.extra->>'refund_amount', '')
                              ~ '^[0-9]+([.][0-9]+)?$'
                         THEN (q.extra->>'refund_amount')::numeric
                         ELSE 0
                       END
                     ), 0) AS refund_amount,
                     0::numeric AS internal_spend, 0::numeric AS external_spend
              FROM qianniu_daily q
              WHERE q.tenant_id = :tenant_id
                AND q.date BETWEEN :date_from AND :date_to
              GROUP BY {q_bucket}
              UNION ALL
              SELECT {a_bucket} AS d, 0, 0,
                     COALESCE(SUM(a.cost), 0), 0
              FROM ad_daily a
              WHERE a.tenant_id = :tenant_id
                AND a.date BETWEEN :date_from AND :date_to
              GROUP BY {a_bucket}
              UNION ALL
              SELECT {p_bucket} AS d, 0, 0, 0,
                     COALESCE(SUM(p.quote_amount), 0)
              FROM promotion p
              WHERE p.tenant_id = :tenant_id AND p.is_active = true
                AND p.publish_status = '已发布'
                AND p.cooperation_date BETWEEN :date_from AND :date_to
              GROUP BY {p_bucket}
            ) source
            GROUP BY d
            ORDER BY d
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

    async def published_spend_by_style(
        self, *, tenant_id: UUID, date_from: date, date_to: date
    ) -> list[Mapping[str, Any]]:
        sql = text(
            """
            SELECT p.style_id, COALESCE(SUM(p.quote_amount), 0) AS external_spend
            FROM promotion p
            WHERE p.tenant_id = :tenant_id AND p.is_active = true
              AND p.publish_status = '已发布'
              AND p.cooperation_date BETWEEN :date_from AND :date_to
            GROUP BY p.style_id
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
    "BiRepository",
    "ProductionRepository",
    "StoreDailyRepository",
    "TargetPlanningRepository",
    "WorkProgressRepository",
    "style_exists",
]
