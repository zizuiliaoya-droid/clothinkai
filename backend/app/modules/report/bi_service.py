"""U17 BI 看板服务（复用 report service 聚合 cards + charts）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.report.production_service import ProductionService
from app.modules.report.store_daily_service import StoreDailyService

DEFAULT_BI_LAYOUT = {
    "cards": ["style_count", "pay_amount", "store_days"],
    "charts": ["store_trend", "style_roi_bar", "style_pay_pie"],
}

_TOP_N = 10


class BiService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_dashboard(
        self, tenant_id: UUID, time_range: tuple[date, date]
    ) -> dict:
        prod = await ProductionService(self._s).get_report(tenant_id, time_range)
        store = await StoreDailyService(self._s).get_dashboard(
            tenant_id, time_range
        )
        pay_total = sum((r.pay_amount for r in prod.items), Decimal("0"))
        top = prod.items[:_TOP_N]
        cards = [
            {"key": "style_count", "label": "在投款式", "value": len(prod.items)},
            {"key": "pay_amount", "label": "支付额", "value": str(pay_total)},
            {"key": "store_days", "label": "店铺天数", "value": len(store)},
        ]
        charts = [
            {
                "type": "line", "title": "店铺支付额趋势",
                "labels": [str(r.date) for r in store],
                "series": [{"name": "支付额",
                            "data": [float(r.pay_amount) for r in store]}],
            },
            {
                "type": "bar", "title": "款式净投产比 Top10",
                "labels": [r.style_code for r in top],
                "series": [{"name": "净投产比",
                            "data": [float(r.net_roi or 0) for r in top]}],
            },
            {
                "type": "pie", "title": "款式支付额占比 Top10",
                "labels": [r.style_code for r in top],
                "series": [{"name": "支付额",
                            "data": [float(r.pay_amount) for r in top]}],
            },
        ]
        return {"cards": cards, "charts": charts}


__all__ = ["BiService", "DEFAULT_BI_LAYOUT"]
