"""U14 WorkProgressService（工作进度表，按月 × PR 聚合）。"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import report_query_duration_seconds
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import WorkProgressRepository
from app.modules.report.advanced_schemas import PrWorkProgress
from app.modules.report.exceptions import ReportInvalidTimeRangeError
from app.services.metric.common import safe_div

_Q4 = Decimal("0.0001")


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

    async def get_for_month(
        self, tenant_id: UUID, month: str
    ) -> list[PrWorkProgress]:
        date_from, date_to = _month_range(month)
        with report_query_duration_seconds.labels("work_progress").time():
            rows = await self._repo.aggregate_by_pr(
                tenant_id=tenant_id,
                date_from=date_from,
                date_to=date_to,
                today=get_today(),
            )
        return [self._to_row(r) for r in rows]

    def _to_row(self, r: Mapping[str, Any]) -> PrWorkProgress:
        quote = int(r["quote_count"])
        publish = int(r["publish_count"])
        return PrWorkProgress(
            pr_id=r["pr_id"],
            pr_name=r["pr_name"],
            quote_count=quote,
            in_schedule_count=int(r["in_schedule_count"]),
            urge_count=int(r["urge_count"]),
            important_urge_count=int(r["important_urge_count"]),
            overdue_count=int(r["overdue_count"]),
            publish_count=publish,
            info_complete_rate=safe_div(
                r["info_complete_count"], publish, quantize=_Q4
            ),
            cancel_count=int(r["cancel_count"]),
            recall_due_count=int(r["recall_due_count"]),
            recall_success_count=int(r["recall_success_count"]),
            recall_complete_rate=safe_div(
                r["recall_success_count"], r["recall_due_count"], quantize=_Q4
            ),
            overdue_rate=safe_div(r["overdue_count"], quote, quantize=_Q4),
            month_complete_rate=safe_div(publish, quote, quantize=_Q4),
            hit_count=int(r["hit_count"]),
            hit_rate=safe_div(r["hit_count"], publish, quantize=_Q4),
            like_count=int(r["like_count"]),
            cost=r["cost"],
            cpl=safe_div(r["cost"], r["like_count"], quantize=_Q4),
        )


__all__ = ["WorkProgressService"]
