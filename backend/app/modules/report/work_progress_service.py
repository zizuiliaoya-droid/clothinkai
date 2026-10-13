"""U14 WorkProgressService（工作进度表，按区间 × PR 聚合）。"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import report_query_duration_seconds
from app.core.security.field_permissions import FieldPermissionContext
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import WorkProgressRepository
from app.modules.report.advanced_schemas import PrWorkProgress
from app.modules.report.drill_service import validate_seasons
from app.modules.report.exceptions import ReportInvalidTimeRangeError
from app.modules.report.field_mask import aggregate_promo_amounts_visible
from app.modules.report.pr_scope import PrScope
from app.modules.report.summary_read import SummaryReadRepository, record_source
from app.modules.report.work_progress_rows import progress_metrics
from app.modules.urge.service import UrgeService


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
        seasons: Sequence[str] | None,
        scope: PrScope,
        field_ctx: FieldPermissionContext,
    ) -> list[PrWorkProgress]:
        """按区间 × PR 聚合。区间每天都有覆盖才读汇总表，否则实时。

        没有「当月永远实时」的特例：未来的日子刷新不到、不会有覆盖，含未来的区间照样走实时。

        ``scope``（只看本人）下推到 SQL（7e R2）：两条路径都按 ``scope.as_param()`` 过滤。
        ``seasons``（7e R5）：只统计归属商品季节命中的单（没归属商品的单不算），空 = 不筛；
        ≤ 20 个、每个 ≤ 64 字，违者 422。
        ``field_ctx`` 给 F3 聚合金额（金额列与金额派生的比率）。
        三个关键字都不给默认值：漏传直接报错，不悄悄放成「全部可见」或「不筛季节」。
        """
        validate_seasons(seasons)
        fresh = await self._summary.freshness(tenant_id, date_from, date_to)
        pr_id = scope.as_param()
        with report_query_duration_seconds.labels("work_progress").time():
            if fresh.source == "summary":
                record_source("work_progress", "summary")
                rows = await self._summary.work_progress_by_pr(
                    tenant_id=tenant_id,
                    date_from=date_from,
                    date_to=date_to,
                    seasons=seasons,
                    pr_id=pr_id,
                )
            else:
                record_source("work_progress", "live")
                rows = await self._repo.aggregate_by_pr(
                    tenant_id=tenant_id,
                    date_from=date_from,
                    date_to=date_to,
                    today=get_today(),
                    thresholds=await self._urge.get_urge_thresholds(tenant_id),
                    by_goods=False,
                    seasons=seasons,
                    pr_id=pr_id,
                )
        amounts = aggregate_promo_amounts_visible("work_progress", field_ctx)
        return [self._to_row(r, amounts=amounts) for r in rows]

    @staticmethod
    def _to_row(r: Mapping[str, Any], *, amounts: bool) -> PrWorkProgress:
        return PrWorkProgress(
            pr_id=r["pr_id"], pr_name=r["pr_name"], **progress_metrics(r, amounts=amounts)
        )


__all__ = ["WorkProgressService"]
