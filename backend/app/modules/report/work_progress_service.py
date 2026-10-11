"""U14 WorkProgressService（工作进度表，按区间 × PR 聚合）。"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import report_query_duration_seconds
from app.core.security.field_permissions import FieldPermissionContext
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import WorkProgressRepository
from app.modules.report.advanced_schemas import PrWorkProgress
from app.modules.report.exceptions import ReportInvalidTimeRangeError
from app.modules.report.field_mask import aggregate_promo_amounts_visible
from app.modules.report.pr_scope import PrScope
from app.modules.report.summary_read import SummaryReadRepository, record_source
from app.modules.urge.service import UrgeService
from app.services.metric.common import safe_div

_Q4 = Decimal("0.0001")
_CENT = Decimal("0.01")


def _month_range(month: str) -> tuple[date, date]:
    """``YYYY-MM`` → 该自然月首尾（旧参数 ``month`` 换算成整月，留一个版本）。"""
    try:
        year, mon = (int(x) for x in month.split("-"))
        first = date(year, mon, 1)
        last = date(year, mon, calendar.monthrange(year, mon)[1])
    except (ValueError, TypeError) as exc:
        raise ReportInvalidTimeRangeError() from exc
    return first, last


class WorkProgressService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = WorkProgressRepository(session)
        self._summary = SummaryReadRepository(session)
        self._urge = UrgeService(session)

    async def get_for_range(
        self,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        *,
        scope: PrScope,
        field_ctx: FieldPermissionContext,
    ) -> list[PrWorkProgress]:
        """按区间 × PR 聚合。区间每天都有覆盖才读汇总表，否则实时。

        没有「当月永远实时」的特例：未来的日子刷新不到、不会有覆盖，含未来的区间照样走实时。

        ``scope``（只看本人）在 SQL **之后**按行过滤，两条路径取的都是全部行，等值不受影响
        （R2 再把过滤下推到 SQL）。``field_ctx`` 给 F3 聚合金额（``cost`` / ``cpl``）。
        两个关键字都不给默认值：漏传直接报错，不悄悄放成「全部可见」。
        """
        fresh = await self._summary.freshness(tenant_id, date_from, date_to)
        with report_query_duration_seconds.labels("work_progress").time():
            if fresh.source == "summary":
                record_source("work_progress", "summary")
                rows = await self._summary.work_progress_by_pr(
                    tenant_id=tenant_id, date_from=date_from, date_to=date_to
                )
            else:
                record_source("work_progress", "live")
                rows = await self._repo.aggregate_by_pr(
                    tenant_id=tenant_id,
                    date_from=date_from,
                    date_to=date_to,
                    today=get_today(),
                    thresholds=await self._urge.get_urge_thresholds(tenant_id),
                )
        amounts = aggregate_promo_amounts_visible("work_progress", field_ctx)
        return [self._to_row(r, amounts=amounts) for r in rows if scope.keeps(r["pr_id"])]

    def _to_row(self, r: Mapping[str, Any], *, amounts: bool) -> PrWorkProgress:
        quote = int(r["quote_count"])
        publish = int(r["publish_count"])
        effective_quote = int(r["effective_quote_count"])
        # 两条路径数值相等但写法可能不同（"0" vs "0.00"），归一成两位小数
        cost = Decimal(str(r["cost"] or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)
        return PrWorkProgress(
            pr_id=r["pr_id"],
            pr_name=r["pr_name"],
            quote_count=quote,
            in_schedule_count=int(r["in_schedule_count"]),
            urge_count=int(r["urge_count"]),
            important_urge_count=int(r["important_urge_count"]),
            overdue_count=int(r["overdue_count"]),
            publish_count=publish,
            info_complete_count=int(r["info_complete_count"]),
            info_complete_rate=safe_div(r["info_complete_count"], publish, quantize=_Q4),
            cancel_count=int(r["cancel_count"]),
            recall_due_count=int(r["recall_due_count"]),
            recall_success_count=int(r["recall_success_count"]),
            recall_complete_rate=safe_div(
                r["recall_success_count"], r["recall_due_count"], quantize=_Q4
            ),
            effective_quote_count=effective_quote,
            # PRD 第 9 章：超时率 / 完成率的分母要扣掉召回量与取消量。
            # 用裸约稿量做分母会把已取消、已召回的单也算成「待发布」，完成率被系统性低估。
            overdue_rate=safe_div(r["overdue_count"], effective_quote, quantize=_Q4),
            month_complete_rate=safe_div(publish, effective_quote, quantize=_Q4),
            hit_count=int(r["hit_count"]),
            hit_rate=safe_div(r["hit_count"], publish, quantize=_Q4),
            like_count=int(r["like_count"]),
            # F3：聚合金额按报表读权限给（field_mask.aggregate_promo_amounts_visible）
            cost=cost if amounts else None,
            cpl=safe_div(cost, r["like_count"], quantize=_Q4) if amounts else None,
        )


__all__ = ["WorkProgressService"]
