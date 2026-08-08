"""U14 ProductionService（投产报表、周环比与单款趋势）。"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import attachment_service
from app.core.metrics import report_query_duration_seconds
from app.modules.report.advanced_repository import ProductionRepository
from app.modules.report.advanced_schemas import ProductionReport, ProductionRow
from app.services.metric import style_roi

# 投产报表 extra 汇总跳过的非指标列（ID/文本/日期类）
_EXTRA_SKIP = {
    "统计日期", "日期", "商品ID", "主商品ID", "主体ID", "主体类型", "主体名称",
    "货号", "商品名称", "商品简称", "商品类型", "商品状态", "商品标签",
}


class ProductionService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = ProductionRepository(session)

    async def get_report(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        *,
        exclude_brushing: bool = True,
        season: str | None = None,
    ) -> ProductionReport:
        cur_from, cur_to = time_range
        span = cur_to - cur_from
        prev_to = cur_from - timedelta(days=1)
        prev_from = prev_to - span
        with report_query_duration_seconds.labels("production").time():
            cur_rows = await self._repo.aggregate_by_style(
                tenant_id=tenant_id, date_from=cur_from, date_to=cur_to,
                exclude_brushing=exclude_brushing, season=season,
            )
            prev_rows = await self._repo.aggregate_by_style(
                tenant_id=tenant_id, date_from=prev_from, date_to=prev_to,
                exclude_brushing=exclude_brushing, season=season,
            )
            extra_by_style = await self._aggregate_extra(
                tenant_id, cur_from, cur_to
            )
        items = []
        for r in cur_rows:
            row = self._to_row(r, exclude_brushing)
            row.extra = extra_by_style.get(str(r["style_id"]), {})
            items.append(row)
        return ProductionReport(
            items=items,
            previous=[self._to_row(r, exclude_brushing) for r in prev_rows],
        )

    async def get_trend(
        self,
        tenant_id: UUID,
        style_id: UUID,
        time_range: tuple[date, date],
        *,
        granularity: str = "day",
        exclude_brushing: bool = True,
    ) -> "ProductionTrend":
        from app.modules.report.advanced_schemas import (
            ProductionTrend,
            ProductionTrendPoint,
        )

        rows = await self._repo.daily_trend_by_style(
            tenant_id=tenant_id,
            style_id=style_id,
            date_from=time_range[0],
            date_to=time_range[1],
            granularity=granularity,
            exclude_brushing=exclude_brushing,
        )
        points: list[ProductionTrendPoint] = []
        for row in rows:
            pay_amount = Decimal(str(row.get("pay_amount") or 0))
            refund_amount = Decimal(str(row.get("refund_amount") or 0))
            confirmed_amount = Decimal(str(row.get("confirmed_amount") or 0))
            promo_cost = Decimal(str(row.get("promo_cost") or 0))
            ad_spend = Decimal(str(row.get("ad_spend") or 0))
            total_spend = Decimal(str(row.get("total_spend") or 0))
            points.append(
                ProductionTrendPoint(
                    date=row["date"],
                    pay_amount=pay_amount,
                    refund_amount=refund_amount,
                    confirmed_amount=confirmed_amount,
                    promo_cost=promo_cost,
                    ad_spend=ad_spend,
                    total_spend=total_spend,
                    net_roi=style_roi.net_roi(
                        confirmed_amount,
                        total_spend,
                        exclude_brushing=exclude_brushing,
                    ),
                )
            )
        return ProductionTrend(points=points)

    async def _aggregate_extra(
        self, tenant_id: UUID, date_from: date, date_to: date
    ) -> dict[str, dict[str, Any]]:
        """按款式 SUM 千牛/站内 extra 的数值列（对齐 final.xlsx 投产报表 70 列）。"""
        rows = await self._repo.fetch_extra_by_style(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        agg: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        for r in rows:
            sid = r["style_id"]
            extra = r["extra"]
            if sid is None or not isinstance(extra, dict):
                continue
            key = str(sid)
            for k, v in extra.items():
                if k in _EXTRA_SKIP or v is None or v == "":
                    continue
                try:
                    num = Decimal(str(v).replace(",", "").replace("%", "").strip())
                except (InvalidOperation, ValueError):
                    continue
                agg[key][k] += num
        return {
            sid: {k: format(val, "f") for k, val in fields.items()}
            for sid, fields in agg.items()
        }

    @staticmethod
    def _to_row(r: Mapping[str, Any], exclude_brushing: bool) -> ProductionRow:
        pay = r["pay_amount"]
        refund = r["refund_amount"]
        confirmed = pay - refund
        total_spend = r["promo_cost"] + r["ad_spend"]
        ret_rate = style_roi.return_rate(refund, pay)
        main_image_url: str | None = None
        main_image_key = r.get("main_image_key")
        if main_image_key:
            try:
                main_image_url = attachment_service.get_signed_url(
                    "private", main_image_key, expires_in=3600
                )
            except Exception:  # noqa: BLE001
                main_image_url = None
        return ProductionRow(
            style_id=r["style_id"],
            style_code=r["style_code"],
            style_name=r["style_name"],
            main_image_url=main_image_url,
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
