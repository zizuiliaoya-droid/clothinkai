"""报表下钻：按区间 / PR / 商品 / 季节 / 发布状态列推广单（7e R1，报表视图设计 2.1）。

在 report 模块、不碰 ``promotion/service.py``（流程线高频改动区）。判权与 PR 范围由路由层
``resolve_pr_scope`` 定好传进来；这里做输入校验、分页与 F2 逐单金额裁剪。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.security.field_permissions import FieldPermissionContext
from app.modules.report.advanced_repository import DrillFilters, DrillRepository
from app.modules.report.advanced_schemas import DrillPromotionPage, DrillPromotionRow
from app.modules.report.exceptions import ReportInvalidTimeRangeError, ReportRangeTooLongError
from app.modules.report.field_mask import promotion_amounts_visible
from app.modules.report.pr_scope import PrScope

MAX_SPAN_DAYS = 366
MAX_SEASONS = 20
MAX_SEASON_LEN = 64


def validate_drill_range(date_from: date, date_to: date) -> None:
    """``date_from ≤ date_to``，跨度 ≤ 366 天（与刷新端点、``resolve_time_range`` 同上限）。"""
    if date_from > date_to:
        raise ReportInvalidTimeRangeError()
    if (date_to - date_from).days > MAX_SPAN_DAYS:
        raise ReportRangeTooLongError()


def validate_seasons(seasons: Sequence[str] | None) -> None:
    """季节多选：≤ 20 个、每个 ≤ 64 字，违者 422。"""
    if not seasons:
        return
    if len(seasons) > MAX_SEASONS:
        raise ValidationError(
            f"season 最多 {MAX_SEASONS} 个", details={"field": "season", "count": len(seasons)}
        )
    if any(len(s) > MAX_SEASON_LEN for s in seasons):
        raise ValidationError(f"season 每个不超过 {MAX_SEASON_LEN} 字", details={"field": "season"})


class DrillService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = DrillRepository(session)

    async def list_promotions(
        self,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        *,
        pr_scope: PrScope,
        goods_id: UUID | None,
        seasons: Sequence[str] | None,
        publish_status: str | None,
        page: int,
        page_size: int,
        field_ctx: FieldPermissionContext,
    ) -> DrillPromotionPage:
        """``pr_scope`` / ``field_ctx`` 不给默认值：漏传直接报错，不悄悄放成「全部可见」。"""
        validate_drill_range(date_from, date_to)
        validate_seasons(seasons)
        filters = DrillFilters(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            pr_scope=pr_scope,
            goods_id=goods_id,
            seasons=list(seasons) if seasons else None,
            publish_status=publish_status,
        )
        total = await self._repo.count(filters)
        rows = await self._repo.page(filters, limit=page_size, offset=(page - 1) * page_size)
        quote_ok, cost_ok = promotion_amounts_visible(field_ctx)
        return DrillPromotionPage(
            items=[_to_row(r, quote_ok=quote_ok, cost_ok=cost_ok) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )


def _to_row(r: Mapping[str, Any], *, quote_ok: bool, cost_ok: bool) -> DrillPromotionRow:
    # 逐个字段取：投影是白名单，SQL 多取了列也不会漏进响应
    return DrillPromotionRow(
        id=r["id"],
        internal_code=r["internal_code"],
        cooperation_date=r["cooperation_date"],
        scheduled_publish_date=r["scheduled_publish_date"],
        actual_publish_date=r["actual_publish_date"],
        pr_id=r["pr_id"],
        pr_name=r["pr_name"],
        blogger_nickname=r["blogger_nickname"],
        platform=r["platform"],
        display_short_name=r["display_short_name"],
        publish_status=r["publish_status"],
        recall_status=r["recall_status"],
        # F2：服务费要 quote_amount 可读；站外推广成本要 quote_amount 与 cost_snapshot 都可读
        quote_amount=r["quote_amount"] if quote_ok else None,
        total_promo_cost=r["total_promo_cost"] if cost_ok else None,
        like_count=r["like_count"],
        detail_url=None,
    )


__all__ = ["DrillService", "validate_drill_range", "validate_seasons"]
