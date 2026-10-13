"""TASK 15 BI 看板统一聚合服务（7e R6 起改读汇总表，设计 2.6）。

BI 没有自己的 SQL：店铺块 = 店铺日行（``StoreDailyService.read_day_rows``）合计 / 分桶，
推广 4 卡与员工工作量 = 工作进度按 PR 行（``WorkProgressService.get_for_range``），
单品块 = 按商品合计的投产行（``ProductionService.get_goods_rows``）。
整页只调一次 ``freshness``，把来源传给三个入口：同一个响应只有一种来源。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.field_permissions import FieldPermissionContext
from app.modules.product.goods_schemas import goods_display_name
from app.modules.report.advanced_repository import BiNotesRepository, TargetPlanningRepository
from app.modules.report.advanced_schemas import (
    BiChartDatum,
    BiDashboard,
    BiGoodsBlock,
    BiGoodsNotes,
    BiGoodsRow,
    BiNotePoint,
    BiPromotionSummary,
    BiStoreBlock,
    BiStoreSummary,
    BiTrendPoint,
    BiWorkloadRow,
    ProductionRow,
    ProductionTrend,
    PrWorkProgress,
    StoreReportRow,
)
from app.modules.report.drill_service import validate_drill_range
from app.modules.report.field_mask import (
    aggregate_promo_amounts_visible,
    promotion_amounts_visible,
)
from app.modules.report.pr_scope import PR_SCOPE_ALL
from app.modules.report.production_service import ProductionService
from app.modules.report.store_buckets import bucket_store_rows, total_store_row
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.summary_read import SummaryReadRepository
from app.modules.report.work_progress_service import WorkProgressService
from app.services.metric.common import safe_div

DEFAULT_BI_LAYOUT = {
    "cards": ["style_count", "pay_amount", "store_days"],
    "charts": ["store_trend", "style_roi_bar", "style_pay_pie"],
}

_TOP_N = 10
_Q4 = Decimal("0.0001")
_ZERO = Decimal("0")
_LABEL_MAX_STYLE_CODES = 3
_OTHER_LABEL = "其他"
# 散点最多点数（设计 2.6）：超出按发布日期取最近这么多，响应标 truncated
MAX_NOTE_POINTS = 500


def bi_chart_labels(rows: Sequence[ProductionRow]) -> list[str]:
    """BI 图表标签（补充 2，§11.1）：显示名 + 套装标记，不出现商品编码。

    同一张图里显示名重复的，追加成员款号「（款号 A、B）」区分，超过 3 个写前 3 个加「等」。
    """
    names = [
        goods_display_name(row.goods_title, row.goods_short_name)
        + ("（套装）" if row.is_suit else "")
        for row in rows
    ]
    counts = Counter(names)
    labels: list[str] = []
    for name, row in zip(names, rows, strict=True):
        if counts[name] > 1 and row.style_codes:
            codes = "、".join(row.style_codes[:_LABEL_MAX_STYLE_CODES])
            more = "等" if len(row.style_codes) > _LABEL_MAX_STYLE_CODES else ""
            name = f"{name}（款号 {codes}{more}）"
        labels.append(name)
    return labels


class BiService:
    def __init__(self, session: AsyncSession) -> None:
        self._summary = SummaryReadRepository(session)
        self._store = StoreDailyService(session)
        self._work = WorkProgressService(session)
        self._production = ProductionService(session)
        self._targets = TargetPlanningRepository(session)
        self._notes = BiNotesRepository(session)

    async def get_dashboard(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        *,
        granularity: str = "day",
        exclude_brushing: bool,
        field_ctx: FieldPermissionContext,
    ) -> BiDashboard:
        """``exclude_brushing`` 管单品块（ROI 扣不扣刷单）；店铺块是全店口径，照 2.4 两种投产都给。

        ``field_ctx`` 给 F3（推广汇总 4 个金额）；两个关键字都不给默认值，漏传直接报错。
        """
        date_from, date_to = time_range
        # 整页一次：三个入口都照这个来源读，不各自判（否则一半汇总、一半实时）
        source = (await self._summary.freshness(tenant_id, date_from, date_to)).source

        day_rows = await self._store.read_day_rows(
            tenant_id, date_from, date_to, "bi_store", source=source
        )
        work_rows = await self._work.get_for_range(
            tenant_id,
            date_from,
            date_to,
            seasons=None,
            scope=PR_SCOPE_ALL,
            field_ctx=field_ctx,
            source=source,
            label="bi_workload",
        )
        targets = await self._targets.targets_by_pr(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to
        )
        goods_rows = await self._production.get_goods_rows(
            tenant_id,
            time_range,
            exclude_brushing=exclude_brushing,
            use_summary=True,
            source=source,
            label="bi_goods",
        )

        store = BiStoreBlock(
            summary=self._store_summary(total_store_row(day_rows, date_from, date_to)),
            trend=[
                self._trend_point(row)
                for row in bucket_store_rows(day_rows, granularity, date_from, date_to)
            ],
            promotion=self._promotion_summary(
                work_rows, amounts=aggregate_promo_amounts_visible("bi", field_ctx)
            ),
            # 未分配行（pr_id 为空）没有目标篇数
            workload=[
                self._workload_row(row, 0 if row.pr_id is None else targets.get(row.pr_id, 0))
                for row in work_rows
            ],
        )
        return BiDashboard(
            date_from=date_from,
            date_to=date_to,
            granularity=granularity,
            exclude_brushing=exclude_brushing,
            store=store,
            goods=self._goods_block(goods_rows),
        )

    async def get_goods_trend(
        self,
        tenant_id: UUID,
        goods_id: UUID,
        time_range: tuple[date, date],
        *,
        granularity: str,
        exclude_brushing: bool,
    ) -> ProductionTrend:
        """Tab2 单品日趋势：直接用投产趋势（读源判断与参数不变），只是判权挂 ``report.bi:read``。"""
        return await self._production.get_trend(
            tenant_id,
            goods_id,
            time_range,
            granularity=granularity,
            exclude_brushing=exclude_brushing,
        )

    async def get_goods_notes(
        self,
        tenant_id: UUID,
        goods_id: UUID,
        date_from: date,
        date_to: date,
        *,
        field_ctx: FieldPermissionContext,
    ) -> BiGoodsNotes:
        """笔记散点：区间内合作、归属该商品、已发布且录过 7 天数据的单，按发布日期升序。

        超过 500 张取发布日期最近的 500 张，``truncated`` 为真。商品不存在或不属本租户 → 空结果。
        站外推广成本按 F2（``quote_amount`` 与 ``cost_snapshot`` 都可读才给），读不到给 null。
        """
        validate_drill_range(date_from, date_to)
        rows = await self._notes.notes(
            tenant_id=tenant_id,
            goods_id=goods_id,
            date_from=date_from,
            date_to=date_to,
            limit=MAX_NOTE_POINTS + 1,
        )
        truncated = len(rows) > MAX_NOTE_POINTS
        rows = rows[:MAX_NOTE_POINTS]
        cost_ok = promotion_amounts_visible(field_ctx)[1]
        return BiGoodsNotes(
            goods_id=goods_id,
            date_from=date_from,
            date_to=date_to,
            truncated=truncated,
            # SQL 按发布日期倒序取最近的，响应翻成升序（横轴从左到右）
            points=[
                BiNotePoint(
                    promotion_id=r["promotion_id"],
                    publish_date=r["publish_date"],
                    like_count=r["like_count"],
                    promo_cost=r["promo_cost"] if cost_ok else None,
                    blogger_nickname=r["blogger_nickname"],
                    platform=r["platform"],
                    detail_url=None,
                )
                for r in reversed(rows)
            ],
        )

    @staticmethod
    def _store_summary(row: StoreReportRow) -> BiStoreSummary:
        return BiStoreSummary(
            pay_amount=row.pay_amount,
            refund_amount=row.refund_amount,
            return_rate=safe_div(row.refund_amount, row.pay_amount, quantize=_Q4),
            brushing_amount=row.brushing_amount,
            actual_sales=row.actual_sales,
            internal_spend=row.internal_spend,
            external_spend=row.promo_cost,
            total_spend=row.total_spend,
            total_spend_ratio=row.total_spend_ratio,
            internal_spend_ratio=row.internal_ratio,
            external_spend_ratio=row.external_ratio,
            net_roi=row.net_roi,
            roi_ex_brushing=row.roi_ex_brushing,
        )

    @staticmethod
    def _trend_point(row: StoreReportRow) -> BiTrendPoint:
        return BiTrendPoint(
            date_from=row.date_from,
            date_to=row.date_to,
            pay_amount=row.pay_amount,
            refund_amount=row.refund_amount,
            actual_sales=row.actual_sales,
            internal_spend=row.internal_spend,
            external_spend=row.promo_cost,
            total_spend=row.total_spend,
            net_roi=row.net_roi,
        )

    @staticmethod
    def _promotion_summary(rows: Sequence[PrWorkProgress], *, amounts: bool) -> BiPromotionSummary:
        """工作进度按 PR 行求和：约稿 / 已发布 / 未发布 / 已取消的服务费与单数。"""
        count = sum(r.quote_count for r in rows)
        published = sum(r.publish_count for r in rows)

        # F3：聚合金额按报表读权限给（field_mask.aggregate_promo_amounts_visible）
        def amount(key: str) -> Decimal | None:
            if not amounts:
                return None
            return sum((getattr(r, key) or _ZERO for r in rows), _ZERO)

        return BiPromotionSummary(
            commission_amount=amount("quote_amount"),
            commission_count=count,
            published_spend=amount("published_quote_amount"),
            published_count=published,
            unpublished_spend=amount("unpublished_quote_amount"),
            unpublished_count=sum(r.unpublished_count for r in rows),
            cancelled_amount=amount("cancelled_quote_amount"),
            cancelled_count=sum(r.cancel_count for r in rows),
            publish_rate=safe_div(published, count, quantize=_Q4),
        )

    @staticmethod
    def _workload_row(row: PrWorkProgress, target: int) -> BiWorkloadRow:
        return BiWorkloadRow(
            pr_id=row.pr_id,
            pr_name=row.pr_name,
            target_count=target,
            quote_count=row.quote_count,
            quote_progress=safe_div(row.quote_count, target, quantize=_Q4),
            publish_count=row.publish_count,
            publish_progress=safe_div(row.publish_count, row.quote_count, quantize=_Q4),
            pending_count=row.unpublished_count,
            cancel_count=row.cancel_count,
            overdue_count=row.overdue_count,
        )

    @staticmethod
    def _goods_row(row: ProductionRow) -> BiGoodsRow:
        return BiGoodsRow(
            goods_id=row.goods_id,
            goods_title=row.goods_title,
            goods_short_name=row.goods_short_name,
            is_suit=row.is_suit,
            style_codes=list(row.style_codes),
            main_image_url=row.main_image_url,
            pay_amount=row.pay_amount,
            refund_amount=row.refund_amount,
            return_rate=row.return_rate,
            confirmed_amount=row.confirmed_amount,
            internal_spend=row.ad_spend,
            external_spend=row.promo_cost,
            total_spend=row.total_spend,
            net_roi=row.net_roi,
        )

    @classmethod
    def _goods_block(cls, rows: Sequence[ProductionRow]) -> BiGoodsBlock:
        """单品表 + ROI Top10（空 ROI 不进，不再画成 0）+ 支付 Top10 与「其他」。"""
        with_roi = [(r, r.net_roi) for r in rows if r.net_roi is not None]
        with_roi.sort(key=lambda pair: pair[1], reverse=True)
        roi_top = with_roi[:_TOP_N]
        roi_labels = bi_chart_labels([r for r, _ in roi_top])
        by_pay = sorted(rows, key=lambda r: r.pay_amount, reverse=True)
        pay_top, rest = by_pay[:_TOP_N], by_pay[_TOP_N:]
        pay_share = [
            BiChartDatum(goods_id=r.goods_id, label=label, value=r.pay_amount)
            for r, label in zip(pay_top, bi_chart_labels(pay_top), strict=True)
        ]
        other = sum((r.pay_amount for r in rest), _ZERO)
        if other > 0:
            pay_share.append(BiChartDatum(label=_OTHER_LABEL, value=other))
        return BiGoodsBlock(
            items=[cls._goods_row(r) for r in rows],
            roi_top=[
                BiChartDatum(goods_id=r.goods_id, label=label, value=roi)
                for (r, roi), label in zip(roi_top, roi_labels, strict=True)
            ],
            pay_share=pay_share,
        )


__all__ = ["DEFAULT_BI_LAYOUT", "MAX_NOTE_POINTS", "BiService", "bi_chart_labels"]
