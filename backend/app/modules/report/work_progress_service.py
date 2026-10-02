"""U14 WorkProgressService（工作进度表，按月 × PR 聚合）。"""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import report_query_duration_seconds
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import WorkProgressRepository
from app.modules.report.advanced_schemas import PrWorkProgress
from app.modules.report.exceptions import ReportInvalidTimeRangeError
from app.modules.report.summary_read import SummaryReadRepository, record_source
from app.modules.urge.service import UrgeService
from app.services.metric.common import safe_div

_Q4 = Decimal("0.0001")
_CENT = Decimal("0.01")


def _month_range(month: str) -> tuple[date, date]:
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

    async def get_for_month(self, tenant_id: UUID, month: str) -> list[PrWorkProgress]:
        """按月 × PR 聚合。整月被汇总表覆盖就读汇总表，否则实时。

        「当月」查的是整个自然月，月底那些还没到的日子刷新不到，所以当月始终实时 ——
        PR 盯着看的恰好是当月，这一点对他们反而是好事；往月读汇总表。
        """
        date_from, date_to = _month_range(month)
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
        return [self._to_row(r) for r in rows]

    def _to_row(self, r: Mapping[str, Any]) -> PrWorkProgress:
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
            cost=cost,
            cpl=safe_div(cost, r["like_count"], quantize=_Q4),
        )


__all__ = ["WorkProgressService"]
