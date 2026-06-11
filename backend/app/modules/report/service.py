"""U08 发文进度看板编排（PublishProgressService）。

按 P-U08-01/03：解析 TimeRange → repo 聚合 → safe_div 组装读模型 + level 着色。
纯读，无写/无事务/不触发事件。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.urge_calculator import get_today
from app.modules.report.domain import (
    level_overdue_rate,
    level_publish_rate,
)
from app.modules.report.exceptions import ReportStyleNotFoundError
from app.modules.report.repository import PublishProgressRepository
from app.modules.report.schemas import (
    PrDetail,
    ProgressSummary,
    StyleCard,
    StyleCardPage,
    TimeSeriesPoint,
)
from app.services.metric.common import safe_div

_Q4 = Decimal("0.0001")
_URGE_DAYS = 10
_IMPORTANT_DAYS = 3


class PublishProgressService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = PublishProgressRepository(session)

    def _common(self, tenant_id: UUID, time_range: tuple) -> dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "date_from": time_range[0],
            "date_to": time_range[1],
            "today": get_today(),
            "urge_days": _URGE_DAYS,
            "important_days": _IMPORTANT_DAYS,
        }

    async def get_summary(
        self, tenant_id: UUID, time_range: tuple
    ) -> ProgressSummary:
        row = await self._repo.aggregate_summary(**self._common(tenant_id, time_range))
        quote = int(row["quote_count"])
        publish_rate = safe_div(row["publish_count"], quote, quantize=_Q4)
        overdue_rate = safe_div(row["overdue_count"], quote, quantize=_Q4)
        cpl = safe_div(row["cooperation_amount"], row["like_count"], quantize=_Q4)
        return ProgressSummary(
            quote_count=quote,
            quote_amount=row["quote_amount"],
            cooperation_amount=row["cooperation_amount"],
            publish_count=int(row["publish_count"]),
            publish_rate=publish_rate,
            publish_rate_level=level_publish_rate(publish_rate),
            overdue_count=int(row["overdue_count"]),
            overdue_rate=overdue_rate,
            overdue_rate_level=level_overdue_rate(overdue_rate),
            like_count=int(row["like_count"]),
            cpl=cpl,
            cancel_count=int(row["cancel_count"]),
        )

    async def get_cards(
        self, tenant_id: UUID, time_range: tuple, *, page: int, page_size: int
    ) -> StyleCardPage:
        rows, total = await self._repo.aggregate_cards(
            **self._common(tenant_id, time_range), page=page, page_size=page_size
        )
        return StyleCardPage(
            items=[self._to_card(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
        )

    def _to_card(self, r: Mapping[str, Any]) -> StyleCard:
        quote = int(r["quote_count"])
        return StyleCard(
            style_id=r["style_id"],
            style_code=r["style_code"],
            style_name=r["style_name"],
            main_image_key=r["main_image_key"],
            cost=r["cost"],
            quote_count=quote,
            quote_amount=r["quote_amount"],
            publish_count=int(r["publish_count"]),
            cooperation_amount=r["cooperation_amount"],
            cancel_count=int(r["cancel_count"]),
            overdue_count=int(r["overdue_count"]),
            like_count=int(r["like_count"]),
            cpl=safe_div(r["cooperation_amount"], r["like_count"], quantize=_Q4),
            publish_rate=safe_div(r["publish_count"], quote, quantize=_Q4),
            overdue_rate=safe_div(r["overdue_count"], quote, quantize=_Q4),
        )

    async def get_detail_by_pr(
        self, tenant_id: UUID, style_id: UUID, time_range: tuple
    ) -> list[PrDetail]:
        if not await self._repo.style_exists(tenant_id, style_id):
            raise ReportStyleNotFoundError()
        rows = await self._repo.aggregate_by_pr(
            style_id=style_id, **self._common(tenant_id, time_range)
        )
        return [
            PrDetail(
                pr_id=r["pr_id"],
                pr_name=r["pr_name"],
                quote_count=int(r["quote_count"]),
                publish_count=int(r["publish_count"]),
                overdue_count=int(r["overdue_count"]),
                like_count=int(r["like_count"]),
                publish_rate=safe_div(
                    r["publish_count"], r["quote_count"], quantize=_Q4
                ),
            )
            for r in rows
        ]

    async def get_detail_by_time(
        self, tenant_id: UUID, style_id: UUID, time_range: tuple
    ) -> list[TimeSeriesPoint]:
        if not await self._repo.style_exists(tenant_id, style_id):
            raise ReportStyleNotFoundError()
        rows = await self._repo.aggregate_by_half_month(
            tenant_id=tenant_id,
            style_id=style_id,
            date_from=time_range[0],
            date_to=time_range[1],
        )
        return [
            TimeSeriesPoint(
                period_label=r["period_label"],
                quote_count=int(r["quote_count"]),
                publish_count=int(r["publish_count"]),
                like_count=int(r["like_count"]),
            )
            for r in rows
        ]


__all__ = ["PublishProgressService"]
