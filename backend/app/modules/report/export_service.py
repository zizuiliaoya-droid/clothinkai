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

_HEADERS = {
    "work-progress": ["PR", "约篇量", "发布量", "月度完成率", "超时率",
                      "爆文数", "点赞数", "成本", "CPL"],
    "production": ["款号", "款名", "支付额", "退款额", "退货率",
                   "净投产比", "加购成本", "总花费"],
    "store-daily": ["日期", "访客数", "支付额", "支付订单", "广告花费"],
}


def _cell(v: Any) -> Any:
    if v is None:
        return ""
    return str(v) if isinstance(v, Decimal) else v


class ReportExportService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def export(
        self,
        tenant_id: UUID,
        report_type: str,
        time_range: tuple[date, date],
    ) -> StreamingResponse:
        if report_type not in _HEADERS:
            report_export_total.labels(
                report_type=report_type, result="invalid"
            ).inc()
            raise ReportExportTypeInvalidError(
                f"不支持的报表类型: {report_type}"
            )
        rows = await self._fetch_rows(tenant_id, report_type, time_range)
        wb = Workbook(write_only=True)
        ws = wb.create_sheet(report_type)
        ws.append(_HEADERS[report_type])
        for r in rows:
            ws.append([_cell(c) for c in r])
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
        self, tenant_id: UUID, report_type: str, time_range: tuple[date, date]
    ) -> list[list]:
        if report_type == "production":
            rep = await ProductionService(self._s).get_report(
                tenant_id, time_range
            )
            return [
                [r.style_code, r.style_name, r.pay_amount, r.refund_amount,
                 r.return_rate, r.net_roi, r.add_cart_cost, r.total_spend]
                for r in rep.items
            ]
        if report_type == "store-daily":
            rows = await StoreDailyService(self._s).get_dashboard(
                tenant_id, time_range
            )
            return [
                [r.date, r.visitors, r.pay_amount, r.pay_orders, r.ad_spend_total]
                for r in rows
            ]
        # work-progress
        month = f"{time_range[0]:%Y-%m}"
        rows = await WorkProgressService(self._s).get_for_month(
            tenant_id, month
        )
        return [
            [r.pr_name, r.quote_count, r.publish_count, r.month_complete_rate,
             r.overdue_rate, r.hit_count, r.like_count, r.cost, r.cpl]
            for r in rows
        ]


__all__ = ["ReportExportService"]
