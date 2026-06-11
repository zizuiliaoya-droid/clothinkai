"""U14 ProductionService（投产报表 + 周环比 + exclude_brushing 占位）。"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import report_query_duration_seconds
from app.modules.report.advanced_repository import ProductionRepository
from app.modules.report.advanced_schemas import ProductionReport, ProductionRow
from app.services.metric import style_roi


class ProductionService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = ProductionRepository(session)

    async def get_report(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        *,
        exclude_brushing: bool = True,
    ) -> ProductionReport:
        cur_from, cur_to = time_range
        span = cur_to - cur_from
        prev_to = cur_from - timedelta(days=1)
        prev_from = prev_to - span
        with report_query_duration_seconds.labels("production").time():
            cur_rows = await self._repo.aggregate_by_style(
                tenant_id=tenant_id, date_from=cur_from, date_to=cur_to,
                exclude_brushing=exclude_brushing,
            )
            prev_rows = await self._repo.aggregate_by_style(
                tenant_id=tenant_id, date_from=prev_from, date_to=prev_to,
                exclude_brushing=exclude_brushing,
            )
        return ProductionReport(
            items=[self._to_row(r, exclude_brushing) for r in cur_rows],
            previous=[self._to_row(r, exclude_brushing) for r in prev_rows],
        )

    @staticmethod
    def _to_row(r: Mapping[str, Any], exclude_brushing: bool) -> ProductionRow:
        pay = r["pay_amount"]
        refund = r["refund_amount"]
        confirmed = pay - refund
        total_spend = r["promo_cost"] + r["ad_spend"]
        ret_rate = style_roi.return_rate(refund, pay)
        return ProductionRow(
            style_id=r["style_id"],
            style_code=r["style_code"],
            style_name=r["style_name"],
            pay_amount=pay,
            refund_amount=refund,
            return_rate=ret_rate,
            confirmed_amount=confirmed,
            promo_cost=r["promo_cost"],
            ad_spend=r["ad_spend"],
            total_spend=total_spend,
            add_cart_count=int(r["add_cart_count"]),
            add_cart_cost=style_roi.add_to_cart_cost(
                total_spend, r["add_cart_count"]
            ),
            net_roi=style_roi.net_roi(
                confirmed, total_spend, exclude_brushing=exclude_brushing
            ),
            # 加购转化率字段 V1 基础口径缺失 → unit_deal_cost 多为 null
            unit_deal_cost=style_roi.unit_deal_cost(
                style_roi.add_to_cart_cost(total_spend, r["add_cart_count"]),
                None,
                ret_rate,
            ),
        )


__all__ = ["ProductionService"]
