"""U14 ProductionService（投产报表、周环比与单款趋势）。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import attachment_service
from app.core.metrics import report_query_duration_seconds
from app.modules.report.advanced_repository import ProductionRepository
from app.modules.report.advanced_schemas import (
    ProductionReport,
    ProductionRow,
    ProductionTrend,
    ProductionTrendPoint,
)
from app.modules.report.extra_metrics import aggregate_extra
from app.modules.report.summary_read import SummaryReadRepository, record_source
from app.services.metric import style_roi

_CENT = Decimal("0.01")


def _money(value: Any) -> Decimal:
    """金额统一成两位小数。

    汇总表路径与实时路径的数值相等，但 Decimal 的**写法**可能不同：实时 SQL 在没有行时
    给的是字面量 ``0``，汇总表读出来是 ``0.00``。JSON 里一个是 ``"0"`` 一个是 ``"0.00"``，
    页面上就是「¥0」和「¥0.00」—— 切换数据源时用户会看到数字的样子变了。所以两条路径
    都在这里归一。源数据（千牛 / 万相台 / 推广单）的金额本身就是两位小数，这一步只改写法、
    不改数值。
    """
    return Decimal(str(value or 0)).quantize(_CENT, rounding=ROUND_HALF_UP)


class ProductionService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = ProductionRepository(session)
        self._summary = SummaryReadRepository(session)

    async def _goods_rows(
        self,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        *,
        exclude_brushing: bool,
        seasons: Sequence[str] | None,
        categories: Sequence[str] | None,
        use_summary: bool,
        metric_label: str,
    ) -> list[Mapping[str, Any]]:
        """区间被汇总表完整覆盖就读汇总表，否则实时聚合（见 summary_read 模块说明）。"""
        if use_summary:
            fresh = await self._summary.freshness(tenant_id, date_from, date_to)
            if fresh.source == "summary":
                record_source(metric_label, "summary")
                return await self._summary.production_by_goods(
                    tenant_id=tenant_id,
                    date_from=date_from,
                    date_to=date_to,
                    exclude_brushing=exclude_brushing,
                    seasons=seasons,
                    categories=categories,
                )
        record_source(metric_label, "live")
        return await self._repo.aggregate_by_goods(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            exclude_brushing=exclude_brushing,
            seasons=seasons,
            categories=categories,
        )

    async def get_report(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        *,
        exclude_brushing: bool = True,
        seasons: Sequence[str] | None = None,
        categories: Sequence[str] | None = None,
        use_summary: bool = True,
    ) -> ProductionReport:
        """投产报表。``use_summary=False`` 强制实时（BI 看板在整体切汇总表之前用）。

        本期与上期各自判断数据源：「最近 30 天」通常被覆盖，它的上一期往往还没有。
        extra（千牛/万相台导出的其余几十列）汇总表不存，始终实时。
        """
        cur_from, cur_to = time_range
        span = cur_to - cur_from
        prev_to = cur_from - timedelta(days=1)
        prev_from = prev_to - span
        with report_query_duration_seconds.labels("production").time():
            cur_rows = await self._goods_rows(
                tenant_id,
                cur_from,
                cur_to,
                exclude_brushing=exclude_brushing,
                seasons=seasons,
                categories=categories,
                use_summary=use_summary,
                metric_label="production",
            )
            prev_rows = await self._goods_rows(
                tenant_id,
                prev_from,
                prev_to,
                exclude_brushing=exclude_brushing,
                seasons=seasons,
                categories=categories,
                use_summary=use_summary,
                metric_label="production_previous",
            )
            extra_by_goods = await self._aggregate_extra(tenant_id, cur_from, cur_to)
        items = []
        for r in cur_rows:
            row = self._to_row(r, exclude_brushing)
            row.extra = extra_by_goods.get(str(r["goods_id"]), {})
            items.append(row)
        return ProductionReport(
            items=items,
            previous=[self._to_row(r, exclude_brushing) for r in prev_rows],
        )

    async def get_trend(
        self,
        tenant_id: UUID,
        goods_id: UUID,
        time_range: tuple[date, date],
        *,
        granularity: str = "day",
        exclude_brushing: bool = True,
        use_summary: bool = True,
    ) -> ProductionTrend:
        date_from, date_to = time_range
        fresh = (
            await self._summary.freshness(tenant_id, date_from, date_to) if use_summary else None
        )
        rows: list[Mapping[str, Any]]
        if fresh is not None and fresh.source == "summary":
            record_source("production_trend", "summary")
            rows = await self._summary.production_trend(
                tenant_id=tenant_id,
                goods_id=goods_id,
                date_from=date_from,
                date_to=date_to,
                granularity=granularity,
                exclude_brushing=exclude_brushing,
            )
        else:
            record_source("production_trend", "live")
            rows = await self._repo.daily_trend_by_goods(
                tenant_id=tenant_id,
                goods_id=goods_id,
                date_from=date_from,
                date_to=date_to,
                granularity=granularity,
                exclude_brushing=exclude_brushing,
            )
        points: list[ProductionTrendPoint] = []
        for row in rows:
            pay_amount = _money(row.get("pay_amount"))
            refund_amount = _money(row.get("refund_amount"))
            confirmed_amount = _money(row.get("confirmed_amount"))
            promo_cost = _money(row.get("promo_cost"))
            ad_spend = _money(row.get("ad_spend"))
            total_spend = _money(row.get("total_spend"))
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
    ) -> dict[str, dict[str, str | None]]:
        """按商品聚合千牛 / 站内 extra（对齐 final.xlsx 投产报表 70 列）。

        规则在 ``extra_metrics``：计数与金额相加，比率 / 均值 / 评分不相加（常用比率按
        分子分母重算）。一个商品的行通常跨多天，累计列不相加（``same_day=False``）。
        """
        rows = await self._repo.fetch_extra_by_goods(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        by_goods: dict[str, list[Any]] = defaultdict(list)
        for r in rows:
            gid = r["goods_id"]
            if gid is None:
                continue
            by_goods[str(gid)].append(r["extra"])
        return {gid: aggregate_extra(extras, same_day=False) for gid, extras in by_goods.items()}

    @staticmethod
    def _to_row(r: Mapping[str, Any], exclude_brushing: bool) -> ProductionRow:
        # 先把金额归一成两位小数再派生 —— 两条数据源路径的输出才会逐字相同
        pay = _money(r["pay_amount"])
        refund = _money(r["refund_amount"])
        promo_cost = _money(r["promo_cost"])
        ad_spend = _money(r["ad_spend"])
        confirmed = pay - refund
        total_spend = promo_cost + ad_spend
        ret_rate = style_roi.return_rate(refund, pay)
        main_image_url: str | None = None
        main_image_key = r.get("main_image_key")
        if main_image_key:
            try:
                main_image_url = attachment_service.get_signed_url(
                    "private", main_image_key, expires_in=3600
                )
            except Exception:
                main_image_url = None
        style_codes_raw = r.get("style_codes")
        return ProductionRow(
            goods_id=r["goods_id"],
            goods_code=r["goods_code"],
            goods_title=r["goods_title"],
            goods_short_name=r["goods_short_name"],
            is_suit=bool(r.get("is_suit")),
            style_codes=style_codes_raw.split(",") if style_codes_raw else [],
            main_image_url=main_image_url,
            pay_amount=pay,
            refund_amount=refund,
            return_rate=ret_rate,
            confirmed_amount=confirmed,
            promo_cost=promo_cost,
            ad_spend=ad_spend,
            total_spend=total_spend,
            add_cart_count=int(r["add_cart_count"]),
            add_cart_cost=style_roi.add_to_cart_cost(total_spend, r["add_cart_count"]),
            net_roi=style_roi.net_roi(confirmed, total_spend, exclude_brushing=exclude_brushing),
            # 加购转化率字段 V1 基础口径缺失 → unit_deal_cost 多为 null
            unit_deal_cost=style_roi.unit_deal_cost(
                style_roi.add_to_cart_cost(total_spend, r["add_cart_count"]),
                None,
                ret_rate,
            ),
        )


__all__ = ["ProductionService"]
