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
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_repository import (
    PRODUCTION_ADDITIVE_COLUMNS,
    ProductionRepository,
)
from app.modules.report.advanced_schemas import (
    ProductionReport,
    ProductionRow,
    ProductionTrend,
    ProductionTrendPoint,
    Recent7Range,
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


def _fold_channels(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """把同一商品的渠道行求和回一行，只给 ``get_goods_rows``（BI / 企微 / AI）用。

    投产接口 R3 起按（商品, 渠道）出行；这几个内部调用方按 ``goods_id`` 挂站外花费 / 去重，
    拆行后会重复挂、多发，所以仍按商品合计（R6 再定）。可加列（金额与计数）先求和，比率由
    ``_to_row`` 在求和之后算；商品信息取首行，``channel`` 去掉。按 ``goods_id`` 首次出现的
    顺序输出，不在 Python 里重排：SQL 已按商品级合计排好、同一商品的渠道行相邻。
    """
    merged: dict[Any, dict[str, Any]] = {}
    for row in rows:
        current = merged.get(row["goods_id"])
        if current is None:
            merged[row["goods_id"]] = {k: v for k, v in row.items() if k != "channel"}
            continue
        for col in PRODUCTION_ADDITIVE_COLUMNS:
            current[col] = current[col] + row[col]
    return list(merged.values())


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
                )
        record_source(metric_label, "live")
        return await self._repo.aggregate_by_goods(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            exclude_brushing=exclude_brushing,
            seasons=seasons,
        )

    async def get_goods_rows(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        *,
        exclude_brushing: bool,
        use_summary: bool,
    ) -> list[ProductionRow]:
        """按商品合计的投产行（``channel=None``、``link_count=0``，不带 extra）。

        给 BI 单品表、企微预警、AI 策略用：它们按 ``goods_id`` 挂数 / 去重，数字同 R2。
        """
        date_from, date_to = time_range
        with report_query_duration_seconds.labels("production").time():
            rows = await self._goods_rows(
                tenant_id,
                date_from,
                date_to,
                exclude_brushing=exclude_brushing,
                seasons=None,
                use_summary=use_summary,
                metric_label="production",
            )
        return [self._to_row(r, exclude_brushing) for r in _fold_channels(rows)]

    async def get_report(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        *,
        exclude_brushing: bool = True,
        seasons: Sequence[str] | None = None,
        use_summary: bool = True,
        show_margin: bool = False,
    ) -> ProductionReport:
        """投产报表。``use_summary=False`` 强制实时（BI 看板在整体切汇总表之前用）。

        本期、上期与近 7 天各自判断数据源：「最近 30 天」通常被覆盖，它的上一期往往还没有。
        extra（千牛/万相台导出的其余几十列）汇总表不存，始终实时。
        ``show_margin``：毛利三列给不给（调用方按 ``field_mask.production_margin_visible`` 传；
        默认 False = 漏传就看不到）。为假时不查成本，三列都是 None；本期与上期同规则。
        """
        cur_from, cur_to = time_range
        span = cur_to - cur_from
        prev_to = cur_from - timedelta(days=1)
        prev_from = prev_to - span
        # 近 7 天（设计 2.3 默 10）：T-7 ~ T-1，按租户时区的今天算，不随筛选变
        today = get_today()
        r7_from, r7_to = today - timedelta(days=7), today - timedelta(days=1)
        with report_query_duration_seconds.labels("production").time():
            cur_rows = await self._goods_rows(
                tenant_id,
                cur_from,
                cur_to,
                exclude_brushing=exclude_brushing,
                seasons=seasons,
                use_summary=use_summary,
                metric_label="production",
            )
            prev_rows = await self._goods_rows(
                tenant_id,
                prev_from,
                prev_to,
                exclude_brushing=exclude_brushing,
                seasons=seasons,
                use_summary=use_summary,
                metric_label="production_previous",
            )
            # 同一份 SQL、同刷单开关与季节，数据源同样各自判断
            recent7_rows = await self._goods_rows(
                tenant_id,
                r7_from,
                r7_to,
                exclude_brushing=exclude_brushing,
                seasons=seasons,
                use_summary=use_summary,
                metric_label="production_recent7",
            )
            extra_by_goods = await self._aggregate_extra(tenant_id, cur_from, cur_to)
            goods_ids = list({r["goods_id"] for r in (*cur_rows, *prev_rows)})
            links = await self._repo.link_counts(tenant_id=tenant_id, goods_ids=goods_ids)
            # 成本读取时现算（不进汇总表）：汇总 / 实时两路共用这一条
            unit_costs = (
                await self._repo.goods_unit_costs(tenant_id=tenant_id, goods_ids=goods_ids)
                if show_margin
                else None
            )
        # 按（商品, 渠道）挂到本期行；近 7 天有、本期没有的键丢弃
        recent7 = {(r["goods_id"], r["channel"]): r for r in recent7_rows}
        items = []
        extra_attached: set[str] = set()
        for r in cur_rows:
            row = self._to_row(r, exclude_brushing, links)
            self._attach_recent7(row, recent7.get((r["goods_id"], r["channel"])), exclude_brushing)
            # extra 是按商品聚合的（不分渠道），只挂在该商品的第一行（普通在前），
            # 否则直播行会把同一份数再显示 / 导出一遍
            gid = str(r["goods_id"])
            if gid not in extra_attached:
                row.extra = extra_by_goods.get(gid, {})
                extra_attached.add(gid)
            items.append(row)
        previous = [self._to_row(r, exclude_brushing, links) for r in prev_rows]
        if unit_costs is not None:
            for row in (*items, *previous):
                self._attach_margin(row, unit_costs.get(row.goods_id))
        return ProductionReport(
            items=items,
            previous=previous,
            recent7_range=Recent7Range(date_from=r7_from, date_to=r7_to),
        )

    @staticmethod
    def _attach_margin(row: ProductionRow, unit_cost: Decimal | None) -> None:
        """毛利三列（只在 ``show_margin`` 时调）；成本缺 → 三列都 None。"""
        row.goods_cost_total, row.gross_profit, row.gross_margin_rate = style_roi.gross_margin(
            row.confirmed_amount, row.pay_orders, unit_cost
        )

    @staticmethod
    def _attach_recent7(
        row: ProductionRow, r7: Mapping[str, Any] | None, exclude_brushing: bool
    ) -> None:
        """近 7 天三列：金额先归一两位小数再派生（同 ``_to_row``）；没有数据 → 0.00 / 0.00 / None。"""
        if r7 is None:
            pay = refund = total_spend = _money(0)
        else:
            pay = _money(r7["pay_amount"])
            refund = _money(r7["refund_amount"])
            total_spend = _money(r7["promo_cost"]) + _money(r7["ad_spend"])
        row.recent7_pay_amount = pay
        row.recent7_total_spend = total_spend
        row.recent7_net_roi = style_roi.net_roi(
            pay - refund, total_spend, exclude_brushing=exclude_brushing
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
        分子分母重算）。累计列只在查询区间是同一天时相加（一个商品挂多条链接，同一天的
        累计值可以加）；区间跨天就不加，与店铺数据按日 / 按周的规则一致。
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
        same_day = date_from == date_to
        return {gid: aggregate_extra(extras, same_day=same_day) for gid, extras in by_goods.items()}

    @staticmethod
    def _to_row(
        r: Mapping[str, Any],
        exclude_brushing: bool,
        link_counts: Mapping[tuple[Any, str], int] | None = None,
    ) -> ProductionRow:
        """``r`` 带 ``channel`` 时是（商品, 渠道）行；按商品合计的行没有 ``channel``。"""
        # 先把金额归一成两位小数再派生 —— 两条数据源路径的输出才会逐字相同
        pay = _money(r["pay_amount"])
        refund = _money(r["refund_amount"])
        promo_cost = _money(r["promo_cost"])
        ad_spend = _money(r["ad_spend"])
        confirmed = pay - refund
        total_spend = promo_cost + ad_spend
        pay_orders = int(r["pay_orders"])
        ad_add_cart = int(r["ad_add_cart_count"])
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
        channel = r.get("channel")
        link_count = 0
        if channel is not None and link_counts is not None:
            link_count = link_counts.get((r["goods_id"], channel), 0)
        return ProductionRow(
            goods_id=r["goods_id"],
            goods_code=r["goods_code"],
            goods_title=r["goods_title"],
            goods_short_name=r["goods_short_name"],
            is_suit=bool(r.get("is_suit")),
            style_codes=style_codes_raw.split(",") if style_codes_raw else [],
            main_image_url=main_image_url,
            channel=channel,
            link_count=link_count,
            pay_amount=pay,
            refund_amount=refund,
            return_rate=ret_rate,
            confirmed_amount=confirmed,
            promo_cost=promo_cost,
            ad_spend=ad_spend,
            total_spend=total_spend,
            pay_orders=pay_orders,
            add_cart_count=int(r["add_cart_count"]),
            add_cart_cost=style_roi.add_to_cart_cost(total_spend, r["add_cart_count"]),
            ad_add_cart_count=ad_add_cart,
            ad_add_cart_cost=style_roi.add_to_cart_cost(ad_spend, ad_add_cart),
            offsite_add_cart=style_roi.offsite_add_cart(
                int(r["paired_all_cart"]), int(r["paired_ad_cart"]), int(r["paired_days"])
            ),
            pre_return_roi=style_roi.pre_return_roi(pay, total_spend),
            net_roi=style_roi.net_roi(confirmed, total_spend, exclude_brushing=exclude_brushing),
            unit_deal_cost=style_roi.promo_cost_per_unit(total_spend, pay_orders),
        )


__all__ = ["ProductionService"]
