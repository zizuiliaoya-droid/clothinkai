"""TASK 15 BI 看板统一聚合服务。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import BiRepository
from app.modules.report.advanced_schemas import (
    BiDashboard,
    BiPromotionSummary,
    BiStoreSummary,
    BiStylePerformance,
    BiTrendPoint,
    BiWorkloadRow,
    ProductionRow,
)
from app.modules.report.production_service import ProductionService
from app.services.metric.common import safe_div

DEFAULT_BI_LAYOUT = {
    "cards": ["style_count", "pay_amount", "store_days"],
    "charts": ["store_trend", "style_roi_bar", "style_pay_pie"],
}

_TOP_N = 10
_Q4 = Decimal("0.0001")


class BiService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = BiRepository(session)
        self._production = ProductionService(session)

    async def get_dashboard(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        *,
        granularity: str = "day",
    ) -> BiDashboard:
        date_from, date_to = time_range
        store_row = await self._repo.aggregate_store_summary(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        promotion_row = await self._repo.aggregate_promotion_summary(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        workload_rows = await self._repo.aggregate_workload(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            today=get_today(),
        )
        trend_rows = await self._repo.aggregate_trend(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            granularity=granularity,
        )
        published_rows = await self._repo.published_spend_by_style(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        production = await self._production.get_report(
            tenant_id, time_range, exclude_brushing=True
        )

        store = self._store_summary(store_row)
        promotion = self._promotion_summary(promotion_row)
        workload = [self._workload_row(row) for row in workload_rows]
        published_by_style = {
            row["style_id"]: Decimal(str(row["external_spend"] or 0))
            for row in published_rows
        }
        styles = [
            self._style_row(row, published_by_style.get(row.style_id, Decimal("0")))
            for row in production.items
        ]
        trend = [BiTrendPoint.model_validate(row) for row in trend_rows]

        roi_top = sorted(
            styles,
            key=lambda row: row.roi if row.roi is not None else Decimal("-Infinity"),
            reverse=True,
        )[:_TOP_N]
        sales_top = sorted(styles, key=lambda row: row.sales_amount, reverse=True)[:_TOP_N]
        cards = [
            {"key": "style_count", "label": "在投款式", "value": len(styles)},
            {"key": "pay_amount", "label": "支付额", "value": str(store.sales_amount)},
            {"key": "store_days", "label": "店铺天数", "value": len(trend)},
        ]
        charts = [
            {
                "type": "line",
                "title": "店铺支付额趋势",
                "labels": [str(row.date) for row in trend],
                "series": [
                    {
                        "name": "支付额",
                        "data": [float(row.sales_amount) for row in trend],
                    }
                ],
            },
            {
                "type": "bar",
                "title": "款式净投产比 Top10",
                "labels": [row.style_code for row in roi_top],
                "series": [
                    {
                        "name": "净投产比",
                        "data": [float(row.roi or 0) for row in roi_top],
                    }
                ],
            },
            {
                "type": "pie",
                "title": "款式支付额占比 Top10",
                "labels": [row.style_code for row in sales_top],
                "series": [
                    {
                        "name": "支付额",
                        "data": [float(row.sales_amount) for row in sales_top],
                    }
                ],
            },
        ]
        return BiDashboard(
            date_from=date_from,
            date_to=date_to,
            granularity=granularity,
            store_summary=store,
            promotion_summary=promotion,
            workload=workload,
            style_performance=styles,
            trend=trend,
            cards=cards,
            charts=charts,
        )

    @staticmethod
    def _store_summary(row: Mapping[str, Any]) -> BiStoreSummary:
        sales = Decimal(str(row["sales_amount"] or 0))
        refund = Decimal(str(row["refund_amount"] or 0))
        internal = Decimal(str(row["internal_spend"] or 0))
        external = Decimal(str(row["external_spend"] or 0))
        total = internal + external
        return BiStoreSummary(
            sales_amount=sales,
            refund_amount=refund,
            return_rate=safe_div(refund, sales, quantize=_Q4),
            internal_spend=internal,
            external_spend=external,
            total_spend=total,
            internal_spend_ratio=safe_div(internal, total, quantize=_Q4),
            external_spend_ratio=safe_div(external, total, quantize=_Q4),
            roi=safe_div(sales - refund, total, quantize=_Q4),
        )

    @staticmethod
    def _promotion_summary(row: Mapping[str, Any]) -> BiPromotionSummary:
        count = int(row["commission_count"])
        published = int(row["published_count"])
        return BiPromotionSummary(
            commission_amount=row["commission_amount"],
            commission_count=count,
            published_spend=row["published_spend"],
            published_count=published,
            unpublished_spend=row["unpublished_spend"],
            unpublished_count=int(row["unpublished_count"]),
            cancelled_amount=row["cancelled_amount"],
            cancelled_count=int(row["cancelled_count"]),
            publish_rate=safe_div(published, count, quantize=_Q4),
        )

    @staticmethod
    def _workload_row(row: Mapping[str, Any]) -> BiWorkloadRow:
        target = int(row["target_count"])
        quote = int(row["quote_count"])
        published = int(row["publish_count"])
        return BiWorkloadRow(
            pr_id=row["pr_id"],
            pr_name=row["pr_name"],
            target_count=target,
            quote_count=quote,
            quote_progress=safe_div(quote, target, quantize=_Q4),
            publish_count=published,
            publish_progress=safe_div(published, quote, quantize=_Q4),
            pending_count=int(row["pending_count"]),
            cancel_count=int(row["cancel_count"]),
            overdue_count=int(row["overdue_count"]),
        )

    @staticmethod
    def _style_row(row: ProductionRow, external_spend: Decimal) -> BiStylePerformance:
        internal = row.ad_spend
        total = internal + external_spend
        return BiStylePerformance(
            style_id=row.style_id,
            style_code=row.style_code,
            style_name=row.style_name,
            main_image_url=row.main_image_url,
            sales_amount=row.pay_amount,
            refund_amount=row.refund_amount,
            return_rate=row.return_rate,
            confirmed_amount=row.confirmed_amount,
            internal_spend=internal,
            external_spend=external_spend,
            total_spend=total,
            roi=safe_div(row.confirmed_amount, total, quantize=_Q4),
        )


__all__ = ["BiService", "DEFAULT_BI_LAYOUT"]
