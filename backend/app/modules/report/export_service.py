"""U17 报表导出服务（openpyxl write_only 流式 → StreamingResponse）。"""

from __future__ import annotations

import io
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics import report_export_total
from app.modules.report.exceptions import ReportExportTypeInvalidError
from app.modules.report.production_service import ProductionService
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.work_progress_service import WorkProgressService

_XLSX_MEDIA = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

_REPORT_TYPES = {"work-progress", "production", "store-daily"}
_GRANULARITIES = {"day", "week", "month", "year"}

_WORK_HEADERS = [
    "PR", "约篇量", "档期内", "催发", "重要催发", "超时", "发布量",
    "信息完整数", "信息完整率", "取消量", "待召回", "召回成功", "召回完成率",
    "超时率", "月度完成率", "爆文数", "爆文率", "点赞数", "成本", "CPL",
]
_PRODUCTION_HEADERS = [
    "货号", "款名", "支付额", "退款额", "退货率", "确认金额", "站外花费",
    "站内花费", "总花费", "加购数", "加购成本", "净投产比", "单件成交成本",
]
_STORE_HEADERS = [
    "日期", "访客数", "支付额", "支付订单", "广告花费", "直通车花费", "引力魔方花费",
]


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    # Excel 会把以 =、+、-、@ 开头的字符串解释为公式；动态表头和业务文本
    # 都统一转义。检查首个非空白字符，防止用前导空格绕过。
    if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")):
        return f"'{v}"
    # Excel 数值单元格必须保持可计算/排序；openpyxl 最终仍以 IEEE 754 存储。
    return float(v) if isinstance(v, Decimal) else v


def _numeric_extra(v: Any) -> Any:
    """服务层 extra 为十进制字符串；导出时恢复为 Excel 数值。"""
    if v is None or isinstance(v, (Decimal, int, float)):
        return v
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (ArithmeticError, ValueError):
        return v


def _bucket_date(value: date, granularity: str) -> date:
    if granularity == "week":
        return date.fromordinal(value.toordinal() - value.weekday())
    if granularity == "month":
        return value.replace(day=1)
    if granularity == "year":
        return value.replace(month=1, day=1)
    return value


def _sum_optional(
    current: Decimal | None, value: Decimal | None
) -> Decimal | None:
    if value is None:
        return current
    return (current or Decimal("0")) + value


class ReportExportService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def export(
        self,
        tenant_id: UUID,
        report_type: str,
        time_range: tuple[date, date],
        *,
        exclude_brushing: bool = True,
        season: str | None = None,
        granularity: str = "day",
    ) -> StreamingResponse:
        if report_type not in _REPORT_TYPES:
            report_export_total.labels(
                report_type=report_type, result="invalid"
            ).inc()
            raise ReportExportTypeInvalidError(
                f"不支持的报表类型: {report_type}"
            )
        if granularity not in _GRANULARITIES:
            raise ReportExportTypeInvalidError(
                f"不支持的时间粒度: {granularity}"
            )
        headers, rows = await self._fetch_rows(
            tenant_id,
            report_type,
            time_range,
            exclude_brushing=exclude_brushing,
            season=season,
            granularity=granularity,
        )
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(report_type)
        ws.append([_cell(header) for header in headers])
        for row in rows:
            ws.append([_cell(cell) for cell in row])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        report_export_total.labels(
            report_type=report_type, result="success"
        ).inc()
        fname = f"{report_type}_{time_range[0]}_{time_range[1]}.xlsx"
        return StreamingResponse(
            buf,
            media_type=_XLSX_MEDIA,
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    async def _fetch_rows(
        self,
        tenant_id: UUID,
        report_type: str,
        time_range: tuple[date, date],
        *,
        exclude_brushing: bool,
        season: str | None,
        granularity: str,
    ) -> tuple[list[str], list[list[Any]]]:
        if report_type == "production":
            report = await ProductionService(self._s).get_report(
                tenant_id,
                time_range,
                exclude_brushing=exclude_brushing,
                season=season,
            )
            extra_keys = sorted(
                {key for row in report.items for key in row.extra}
            )
            rows = [
                [
                    row.style_code,
                    row.style_name,
                    row.pay_amount,
                    row.refund_amount,
                    row.return_rate,
                    row.confirmed_amount,
                    row.promo_cost,
                    row.ad_spend,
                    row.total_spend,
                    row.add_cart_count,
                    row.add_cart_cost,
                    row.net_roi,
                    row.unit_deal_cost,
                    *[_numeric_extra(row.extra.get(key)) for key in extra_keys],
                ]
                for row in report.items
            ]
            return [*_PRODUCTION_HEADERS, *extra_keys], rows

        if report_type == "store-daily":
            source_rows = await StoreDailyService(self._s).get_dashboard(
                tenant_id, time_range
            )
            extra_keys = sorted(
                {key for row in source_rows for key in row.extra}
            )
            if granularity == "day":
                rows = [
                    [
                        row.date,
                        row.visitors,
                        row.pay_amount,
                        row.pay_orders,
                        row.ad_spend_total,
                        row.zhitongche_spend,
                        row.yinli_spend,
                        *[_numeric_extra(row.extra.get(key)) for key in extra_keys],
                    ]
                    for row in source_rows
                ]
                return [*_STORE_HEADERS, *extra_keys], rows

            grouped: dict[date, dict[str, Any]] = {}
            for row in source_rows:
                key = _bucket_date(row.date, granularity)
                values = grouped.setdefault(
                    key,
                    {
                        "visitors": 0,
                        "pay_amount": Decimal("0"),
                        "pay_orders": 0,
                        "ad_spend_total": None,
                        "zhitongche_spend": None,
                        "yinli_spend": None,
                        "extra": {},
                    },
                )
                values["visitors"] += row.visitors
                values["pay_amount"] += row.pay_amount
                values["pay_orders"] += row.pay_orders
                for field in (
                    "ad_spend_total",
                    "zhitongche_spend",
                    "yinli_spend",
                ):
                    values[field] = _sum_optional(
                        values[field], getattr(row, field)
                    )
                for extra_key, raw_value in row.extra.items():
                    numeric = _numeric_extra(raw_value)
                    if isinstance(numeric, Decimal):
                        values["extra"][extra_key] = (
                            values["extra"].get(extra_key, Decimal("0"))
                            + numeric
                        )
            rows = []
            for key in sorted(grouped):
                values = grouped[key]
                rows.append(
                    [
                        key,
                        values["visitors"],
                        values["pay_amount"],
                        values["pay_orders"],
                        values["ad_spend_total"],
                        values["zhitongche_spend"],
                        values["yinli_spend"],
                        *[values["extra"].get(extra_key) for extra_key in extra_keys],
                    ]
                )
            return [*_STORE_HEADERS, *extra_keys], rows

        month = f"{time_range[0]:%Y-%m}"
        work_rows = await WorkProgressService(self._s).get_for_month(
            tenant_id, month
        )
        rows = [
            [
                row.pr_name,
                row.quote_count,
                row.in_schedule_count,
                row.urge_count,
                row.important_urge_count,
                row.overdue_count,
                row.publish_count,
                row.info_complete_count,
                row.info_complete_rate,
                row.cancel_count,
                row.recall_due_count,
                row.recall_success_count,
                row.recall_complete_rate,
                row.overdue_rate,
                row.month_complete_rate,
                row.hit_count,
                row.hit_rate,
                row.like_count,
                row.cost,
                row.cpl,
            ]
            for row in work_rows
        ]
        return _WORK_HEADERS, rows


__all__ = ["ReportExportService"]
