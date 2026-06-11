"""U08 report 聚合仓储（PublishProgressRepository）。

只读聚合 promotion（U04）+ style（U02）+ user（U01）；RLS 自动按 tenant 隔离
（依赖 Session 注入 tenant_id，不显式写 tenant_id WHERE）。

复用 U04 ``URGE_STATUS_SQL_EXPR``（:today/:urge_days/:important_days 参数）+
``services.metric.publish_progress.like_sum_expr``（折算系数来自 U04 常量）。
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

_LIKE = like_sum_expr("like_count")
_OVERDUE = f"COUNT(*) FILTER (WHERE ({URGE_STATUS_SQL_EXPR}) = '超时')"


class PublishProgressRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def _params(
        self, tenant_id: UUID, date_from: date, date_to: date, today: date,
        urge_days: int, important_days: int, **extra: Any
    ) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id, "date_from": date_from, "date_to": date_to,
            "today": today, "urge_days": urge_days,
            "important_days": important_days, **extra,
        }

    async def aggregate_summary(
        self, *, tenant_id, date_from, date_to, today, urge_days, important_days
    ) -> Mapping[str, Any]:
        # 显式 tenant_id 过滤（与 U04 list_with_cte 一致，RLS 之外的防御层）
        sql = text(
            f"""
            SELECT
              COUNT(*) AS quote_count,
              COALESCE(SUM(quote_amount), 0) AS quote_amount,
              COALESCE(SUM(quote_amount) FILTER (WHERE publish_status='已发布'), 0)
                AS cooperation_amount,
              COUNT(*) FILTER (WHERE publish_status='已发布') AS publish_count,
              COUNT(*) FILTER (WHERE publish_status='已取消') AS cancel_count,
              {_OVERDUE} AS overdue_count,
              {_LIKE} AS like_count
            FROM promotion
            WHERE tenant_id = :tenant_id
              AND is_active = true
              AND cooperation_date BETWEEN :date_from AND :date_to
            """
        )
        return (
            await self._s.execute(
                sql,
                self._params(
                    tenant_id, date_from, date_to, today, urge_days, important_days
                ),
            )
        ).mappings().one()

    async def aggregate_cards(
        self, *, tenant_id, date_from, date_to, today, urge_days, important_days,
        page: int, page_size: int
    ) -> tuple[list[Mapping[str, Any]], int]:
        where = (
            "WHERE p.tenant_id = :tenant_id "
            "AND p.is_active = true "
            "AND p.cooperation_date BETWEEN :date_from AND :date_to"
        )
        # total = 不同 style 数
        total_sql = text(
            f"SELECT COUNT(DISTINCT p.style_id) FROM promotion p {where}"
        )
        params = self._params(
            tenant_id, date_from, date_to, today, urge_days, important_days
        )
        total = int((await self._s.execute(total_sql, params)).scalar_one())

        data_sql = text(
            f"""
            SELECT
              p.style_id AS style_id,
              s.style_code AS style_code,
              s.style_name AS style_name,
              s.main_image_key AS main_image_key,
              COALESCE(SUM(p.cost_snapshot), 0) AS cost,
              COUNT(*) AS quote_count,
              COALESCE(SUM(p.quote_amount), 0) AS quote_amount,
              COUNT(*) FILTER (WHERE p.publish_status='已发布') AS publish_count,
              COALESCE(SUM(p.quote_amount) FILTER (WHERE p.publish_status='已发布'), 0)
                AS cooperation_amount,
              COUNT(*) FILTER (WHERE p.publish_status='已取消') AS cancel_count,
              COUNT(*) FILTER (WHERE ({URGE_STATUS_SQL_EXPR}) = '超时') AS overdue_count,
              {like_sum_expr("p.like_count")} AS like_count
            FROM promotion p
            JOIN style s ON s.id = p.style_id
            {where}
            GROUP BY p.style_id, s.style_code, s.style_name, s.main_image_key
            ORDER BY quote_count DESC
            LIMIT :limit OFFSET :offset
            """
        )
        # cards 的 URGE_EXPR 用裸列名（style 无同名列，JOIN 下不歧义）
        params2 = dict(params)
        params2["limit"] = page_size
        params2["offset"] = (page - 1) * page_size
        rows = (await self._s.execute(data_sql, params2)).mappings().all()
        return list(rows), total

    async def aggregate_by_pr(
        self, *, tenant_id: UUID, style_id: UUID, date_from, date_to,
        today, urge_days, important_days
    ) -> list[Mapping[str, Any]]:
        sql = text(
            f"""
            SELECT
              p.pr_id AS pr_id,
              COALESCE(u.display_name, u.username, '未分配') AS pr_name,
              COUNT(*) AS quote_count,
              COUNT(*) FILTER (WHERE p.publish_status='已发布') AS publish_count,
              COUNT(*) FILTER (WHERE ({URGE_STATUS_SQL_EXPR}) = '超时') AS overdue_count,
              {like_sum_expr("p.like_count")} AS like_count
            FROM promotion p
            LEFT JOIN "user" u ON u.id = p.pr_id
            WHERE p.tenant_id = :tenant_id
              AND p.is_active = true
              AND p.style_id = :style_id
              AND p.cooperation_date BETWEEN :date_from AND :date_to
            GROUP BY p.pr_id, u.display_name, u.username
            ORDER BY quote_count DESC
            """
        )
        params = self._params(
            tenant_id, date_from, date_to, today, urge_days, important_days,
            style_id=style_id,
        )
        return list((await self._s.execute(sql, params)).mappings().all())

    async def aggregate_by_half_month(
        self, *, tenant_id: UUID, style_id: UUID, date_from, date_to
    ) -> list[Mapping[str, Any]]:
        sql = text(
            """
            SELECT
              to_char(cooperation_date, 'YYYY-MM')
                || (CASE WHEN extract(day FROM cooperation_date) <= 15
                         THEN ' 上半月' ELSE ' 下半月' END) AS period_label,
              COUNT(*) AS quote_count,
              COUNT(*) FILTER (WHERE publish_status='已发布') AS publish_count,
              COALESCE(SUM(like_count), 0) AS like_count,
              MIN(cooperation_date) AS period_start
            FROM promotion
            WHERE tenant_id = :tenant_id
              AND is_active = true
              AND style_id = :style_id
              AND cooperation_date BETWEEN :date_from AND :date_to
            GROUP BY period_label
            ORDER BY period_start ASC
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
                    },
                )
            ).mappings().all()
        )

    async def style_exists(self, tenant_id: UUID, style_id: UUID) -> bool:
        sql = text(
            "SELECT 1 FROM style WHERE id = :sid AND tenant_id = :tid LIMIT 1"
        )
        return (
            await self._s.execute(sql, {"sid": style_id, "tid": tenant_id})
        ).scalar_one_or_none() is not None


__all__ = ["PublishProgressRepository"]
