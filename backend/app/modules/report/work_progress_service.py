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
from app.modules.promotion.display_name import normalize_goods_short_name
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import WorkProgressRepository
from app.modules.report.advanced_schemas import (
    PrWorkProgress,
    WorkProgressGoodsPage,
    WorkProgressGoodsRow,
    WorkProgressWeekRow,
    WorkProgressWeeks,
)
from app.modules.report.drill_service import validate_seasons
from app.modules.report.exceptions import ReportInvalidTimeRangeError
from app.modules.report.field_mask import aggregate_promo_amounts_visible
from app.modules.report.pr_scope import PrScope
from app.modules.report.summary_read import Source, SummaryReadRepository, record_source
from app.modules.report.work_progress_rows import (
    goods_sort_key,
    progress_metrics,
    sum_progress_rows,
    week_bounds,
)
from app.modules.urge.service import UrgeService

# 没归属商品的单汇成的那一行（只在「查看全部」最后一页末尾，设计 2.5）
UNASSIGNED_GOODS_NAME = "未归属商品"


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
        source: Source | None = None,
        label: str = "work_progress",
    ) -> list[PrWorkProgress]:
        """按区间 × PR 聚合。区间每天都有覆盖才读汇总表，否则实时。

        没有「当月永远实时」的特例：未来的日子刷新不到、不会有覆盖，含未来的区间照样走实时。

        ``scope``（只看本人）下推到 SQL（7e R2）：两条路径都按 ``scope.as_param()`` 过滤。
        ``seasons``（7e R5）：只统计归属商品季节命中的单（没归属商品的单不算），空 = 不筛；
        ≤ 20 个、每个 ≤ 64 字，违者 422。
        ``field_ctx`` 给 F3 聚合金额（金额列与金额派生的比率）。
        三个关键字都不给默认值：漏传直接报错，不悄悄放成「全部可见」或「不筛季节」。
        ``source`` 给了就照它读、不再判（BI 整页只判一次，7e R6）；None = 自己判。
        ``label`` 是 ``record_source`` / 耗时的标签。
        """
        validate_seasons(seasons)
        if source is None:
            source = (await self._summary.freshness(tenant_id, date_from, date_to)).source
        pr_id = scope.as_param()
        with report_query_duration_seconds.labels(label).time():
            if source == "summary":
                record_source(label, "summary")
                rows = await self._summary.work_progress_by_pr(
                    tenant_id=tenant_id,
                    date_from=date_from,
                    date_to=date_to,
                    seasons=seasons,
                    pr_id=pr_id,
                )
            else:
                record_source(label, "live")
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

    async def get_goods(
        self,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        *,
        seasons: Sequence[str] | None,
        scope: PrScope,
        field_ctx: FieldPermissionContext,
        limit: int,
        offset: int,
    ) -> WorkProgressGoodsPage:
        """单品卡片（7e R5，设计 2.5）：按归属商品聚合，整段汇总 / 实时二选一。

        - 汇总 = ``work_progress_by_goods``；实时 = ``aggregate_by_pr(by_goods=True)`` 按商品相加
          （比率在相加之后才算）。两路之后的显示名、排序、切片都在这里，共用一份
        - 排序见 ``goods_sort_key``；``total`` / ``items`` 只含有归属的商品，没归属的单汇成
          ``unassigned``（选了季节时没有）
        - ``scope``、``seasons`` 同 ``get_for_range``；``limit`` / ``offset`` 由路由校验（1 ~ 100、≥ 0）
        """
        validate_seasons(seasons)
        fresh = await self._summary.freshness(tenant_id, date_from, date_to)
        pr_id = scope.as_param()
        with report_query_duration_seconds.labels("work_progress_goods").time():
            if fresh.source == "summary":
                record_source("work_progress_goods", "summary")
                summary_rows = await self._summary.work_progress_by_goods(
                    tenant_id=tenant_id,
                    date_from=date_from,
                    date_to=date_to,
                    seasons=seasons,
                    pr_id=pr_id,
                )
                merged: dict[UUID | None, Mapping[str, Any]] = {
                    r["goods_main_id"]: r for r in summary_rows
                }
            else:
                record_source("work_progress_goods", "live")
                live_rows = await self._repo.aggregate_by_pr(
                    tenant_id=tenant_id,
                    date_from=date_from,
                    date_to=date_to,
                    today=get_today(),
                    thresholds=await self._urge.get_urge_thresholds(tenant_id),
                    by_goods=True,
                    seasons=seasons,
                    pr_id=pr_id,
                )
                # 同一商品可能跨多个 PR：先把计数与金额相加，比率留给 progress_metrics
                merged = dict(sum_progress_rows(live_rows, key=lambda r: r["goods_main_id"]))

        amounts = aggregate_promo_amounts_visible("work_progress", field_ctx)
        unassigned_raw = merged.pop(None, None)
        meta = await self._repo.goods_display_meta(
            tenant_id, [gid for gid in merged if gid is not None]
        )
        items = sorted(
            (
                self._goods_row(gid, meta[gid], r, amounts=amounts)
                for gid, r in merged.items()
                if gid is not None
            ),
            key=goods_sort_key,
        )
        unassigned = None
        if unassigned_raw is not None:
            unassigned = WorkProgressGoodsRow(
                display_name=UNASSIGNED_GOODS_NAME,
                **progress_metrics(unassigned_raw, amounts=amounts),
            )
        return WorkProgressGoodsPage(
            date_from=date_from,
            date_to=date_to,
            total=len(items),
            limit=limit,
            offset=offset,
            items=items[offset : offset + limit],
            unassigned=unassigned,
        )

    async def get_weeks(
        self,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        *,
        seasons: Sequence[str] | None,
        scope: PrScope,
        field_ctx: FieldPermissionContext,
    ) -> WorkProgressWeeks:
        """周明细（7e R5，Q15）：按合作日期 ISO 周分桶，首尾周截到区间，整段汇总 / 实时二选一。

        - 汇总 = ``work_progress_by_week``；实时 = 逐周（周 ∩ 区间，见 ``week_bounds``）调
          ``aggregate_by_pr(by_goods=False)`` 再跨 PR 相加（≤ 54 次小查询，不给聚合 SQL 加粒度）
        - 比率在相加之后才算；只出有单的周，升序；各周之和 = PR 表合计（K5）
        - ``scope``、``seasons`` 同 ``get_for_range``
        """
        validate_seasons(seasons)
        fresh = await self._summary.freshness(tenant_id, date_from, date_to)
        pr_id = scope.as_param()
        weeks = week_bounds(date_from, date_to)
        with report_query_duration_seconds.labels("work_progress_weeks").time():
            if fresh.source == "summary":
                record_source("work_progress_weeks", "summary")
                summary_rows = await self._summary.work_progress_by_week(
                    tenant_id=tenant_id,
                    date_from=date_from,
                    date_to=date_to,
                    seasons=seasons,
                    pr_id=pr_id,
                )
                merged: dict[date, Mapping[str, Any]] = {r["week_start"]: r for r in summary_rows}
            else:
                record_source("work_progress_weeks", "live")
                today = get_today()
                thresholds = await self._urge.get_urge_thresholds(tenant_id)
                merged = {}
                for week_start, lo, hi in weeks:
                    week_rows = await self._repo.aggregate_by_pr(
                        tenant_id=tenant_id,
                        date_from=lo,
                        date_to=hi,
                        today=today,
                        thresholds=thresholds,
                        by_goods=False,
                        seasons=seasons,
                        pr_id=pr_id,
                    )
                    # 跨 PR 相加用精确值（点赞不逐行取整），比率留给 progress_metrics
                    if week_rows:
                        merged[week_start] = sum_progress_rows(week_rows, key=lambda _r: 0)[0]
        amounts = aggregate_promo_amounts_visible("work_progress", field_ctx)
        items = [
            WorkProgressWeekRow(
                week_start=week_start,
                date_from=lo,
                date_to=hi,
                **progress_metrics(merged[week_start], amounts=amounts),
            )
            for week_start, lo, hi in weeks
            if week_start in merged
        ]
        return WorkProgressWeeks(date_from=date_from, date_to=date_to, items=items)

    @staticmethod
    def _to_row(r: Mapping[str, Any], *, amounts: bool) -> PrWorkProgress:
        return PrWorkProgress(
            pr_id=r["pr_id"], pr_name=r["pr_name"], **progress_metrics(r, amounts=amounts)
        )

    @staticmethod
    def _goods_row(
        goods_id: UUID,
        meta: tuple[str, str | None],
        r: Mapping[str, Any],
        *,
        amounts: bool,
    ) -> WorkProgressGoodsRow:
        title, raw_short = meta
        short = normalize_goods_short_name(raw_short)
        return WorkProgressGoodsRow(
            goods_id=goods_id,
            goods_title=title,
            goods_short_name=short,
            display_name=short or title,
            **progress_metrics(r, amounts=amounts),
        )


__all__ = ["WorkProgressService"]
