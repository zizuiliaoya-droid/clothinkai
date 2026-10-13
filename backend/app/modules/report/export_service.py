"""U17 报表导出服务（openpyxl write_only 流式 → StreamingResponse）。"""

from __future__ import annotations

import io
import sys
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError
from app.core.metrics import report_export_total
from app.core.security.field_permissions import FieldPermissionContext
from app.core.security.permissions import EffectivePermissions
from app.modules.report.advanced_schemas import ProductionRow, WorkProgressMetrics
from app.modules.report.exceptions import ReportExportTypeInvalidError
from app.modules.report.field_mask import production_margin_visible
from app.modules.report.pr_scope import PR_SCOPE_ALL
from app.modules.report.production_columns import (
    PRODUCTION_COLUMNS,
    ProductionColumn,
    export_header,
)
from app.modules.report.production_service import ProductionService
from app.modules.report.store_columns import (
    STORE_COLUMNS,
    STORE_PERIOD_HEADERS,
    StoreExportGranularity,
    store_period_cells,
)
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.work_progress_columns import (
    GOODS_HEADERS,
    PR_HEADERS,
    SHEET_GOODS,
    SHEET_PR,
    SHEET_WEEKS,
    WEEK_HEADERS,
    WORK_PROGRESS_METRIC_COLUMNS,
)
from app.modules.report.work_progress_service import WorkProgressService

_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# 报表类型 → 对应的读权限（7e R1，报表视图设计第 4 节）：路由只挡 report.export:read，
# 这里再判这张报表本身能不能看。财务因此能导工作进度、不能导投产
_READ_PERMISSION: dict[str, tuple[str, str]] = {
    "work-progress": ("report.work_progress", "read"),
    "production": ("report.production", "read"),
    "store-daily": ("report.store_daily", "read"),
    # 店铺日 / 周 / 月三页（7e R6 S11）：列跟页面走；旧 store-daily 的 7 列结构不变
    "store": ("report.store_daily", "read"),
}
_REPORT_TYPES = set(_READ_PERMISSION)
_GRANULARITIES = {"day", "week", "month", "year"}
_STORE_GRANULARITIES = set(STORE_PERIOD_HEADERS)


def exportable_report_types(perms: EffectivePermissions) -> list[str]:
    """当前用户能导出的报表类型（capabilities 用）：持 ``report.export:read`` 且有该报表读权限。

    与 :meth:`ReportExportService.export` 的两道判断同一份 ``_READ_PERMISSION``，按其顺序返回。
    """
    if not perms.has("report.export", "read"):
        return []
    return [t for t, (scope, action) in _READ_PERMISSION.items() if perms.has(scope, action)]


# 单品 sheet 一次取全部商品：service 只按 limit / offset 切片
_ALL_GOODS = sys.maxsize
_STORE_HEADERS = [
    "日期",
    "访客数",
    "支付额",
    "支付订单",
    "广告花费",
    "直通车花费",
    "引力魔方花费",
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


def _production_cell(row: ProductionRow, col: ProductionColumn) -> Any:
    value = getattr(row, col.key)
    return ",".join(value) if col.kind == "codes" else value


def _metric_cells(row: WorkProgressMetrics) -> list[Any]:
    """工作进度指标段（三个 sheet 共用，列顺序见 ``WORK_PROGRESS_METRIC_COLUMNS``）。"""
    return [getattr(row, col.key) for col in WORK_PROGRESS_METRIC_COLUMNS]


def _numeric_extra(v: Any) -> Any:
    """服务层 extra 为十进制字符串；导出时恢复为 Excel 数值。"""
    if v is None or isinstance(v, Decimal | int | float):
        return v
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (ArithmeticError, ValueError):
        return v


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
        seasons: Sequence[str] | None = None,
        granularity: str = "day",
        field_ctx: FieldPermissionContext,
        perms: EffectivePermissions,
    ) -> StreamingResponse:
        """先校验类型（不认识的照旧 400），再判该类型的读权限（缺 → 403）。

        ``perms`` 不给默认值：漏传直接报错，不悄悄放行。工作进度只认 ``read``，
        只持 ``read_own`` 的人导不了（设计 2.5：PR 没有导出权限，不变）。
        """
        if report_type not in _REPORT_TYPES:
            report_export_total.labels(report_type=report_type, result="invalid").inc()
            raise ReportExportTypeInvalidError(f"不支持的报表类型: {report_type}")
        scope, action = _READ_PERMISSION[report_type]
        if not perms.has(scope, action):
            report_export_total.labels(report_type=report_type, result="forbidden").inc()
            raise PermissionDeniedError(
                f"缺少权限 {scope}:{action}",
                details={"required_scope": scope, "required_action": action},
            )
        if granularity not in _GRANULARITIES or (
            report_type == "store" and granularity not in _STORE_GRANULARITIES
        ):
            raise ReportExportTypeInvalidError(f"不支持的时间粒度: {granularity}")
        # 店铺三页的文件名 / sheet 名带粒度：store-day / store-week / store-month
        name = f"store-{granularity}" if report_type == "store" else report_type
        # (sheet 名, 表头, 行)：工作进度三个 sheet（7e R5），其他类型一个、以类型命名
        sheets: list[tuple[str, Sequence[str], list[list[Any]]]]
        if report_type == "work-progress":
            sheets = await self._work_progress_sheets(
                tenant_id, time_range, seasons=seasons, field_ctx=field_ctx
            )
        else:
            headers, rows = await self._fetch_rows(
                tenant_id,
                report_type,
                time_range,
                exclude_brushing=exclude_brushing,
                seasons=seasons,
                granularity=granularity,
                field_ctx=field_ctx,
            )
            sheets = [(name, headers, rows)]
        wb = Workbook(write_only=True)
        for title, sheet_headers, sheet_rows in sheets:
            ws = wb.create_sheet(title)
            ws.append([_cell(header) for header in sheet_headers])
            for row in sheet_rows:
                ws.append([_cell(cell) for cell in row])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        report_export_total.labels(report_type=report_type, result="success").inc()
        fname = f"{name}_{time_range[0]}_{time_range[1]}.xlsx"
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
        seasons: Sequence[str] | None,
        granularity: str,
        field_ctx: FieldPermissionContext,
    ) -> tuple[list[str], list[list[Any]]]:
        if report_type == "production":
            report = await ProductionService(self._s).get_report(
                tenant_id,
                time_range,
                exclude_brushing=exclude_brushing,
                seasons=seasons,
                # F1：毛利三列只给管理员；其他角色 service 给 None → 表头照留、单元格为空
                show_margin=production_margin_visible(field_ctx),
            )
            extra_keys = sorted({key for row in report.items for key in row.extra})
            # 表头与单元格都从共用列定义取（列顺序 = 页面）。导出是数据而不是界面：
            # 没填简称就留空，不拿全称冒充
            headers = [export_header(col, report.recent7_range) for col in PRODUCTION_COLUMNS]
            rows = [
                [
                    *[_production_cell(row, col) for col in PRODUCTION_COLUMNS],
                    *[_numeric_extra(row.extra.get(key)) for key in extra_keys],
                ]
                for row in report.items
            ]
            return [*headers, *extra_keys], rows

        if report_type == "store-daily":
            # 周 / 月 / 年的分桶与 extra 聚合都在 service 里：导出与页面是同一份结果，
            # 这里只逐行写出（extra 算不出来的是 None → 空单元格）
            source_rows = await StoreDailyService(self._s).get_dashboard(
                tenant_id, time_range, granularity=granularity
            )
            extra_keys = sorted({key for row in source_rows for key in row.extra})
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

        if report_type == "store":
            # 店铺日 / 周 / 月三页（7e R6 S11）：行 = 页面那份 get_store_report（按日期升序），
            # 列 = 共用列定义；周 / 月写行上实际起止（桶 ∩ 请求区间），extra 排最后
            store_granularity = cast(StoreExportGranularity, granularity)
            store_report = await StoreDailyService(self._s).get_store_report(
                tenant_id, time_range, store_granularity
            )
            extra_keys = sorted({key for item in store_report.items for key in item.extra})
            headers = [
                *STORE_PERIOD_HEADERS[store_granularity],
                *(col.header for col in STORE_COLUMNS),
                *extra_keys,
            ]
            rows = [
                [
                    *store_period_cells(item, store_granularity),
                    *(getattr(item, col.key) for col in STORE_COLUMNS),
                    *[_numeric_extra(item.extra.get(key)) for key in extra_keys],
                ]
                for item in store_report.items
            ]
            return headers, rows

        raise ReportExportTypeInvalidError(f"不支持的报表类型: {report_type}")

    async def _work_progress_sheets(
        self,
        tenant_id: UUID,
        time_range: tuple[date, date],
        *,
        seasons: Sequence[str] | None,
        field_ctx: FieldPermissionContext,
    ) -> list[tuple[str, Sequence[str], list[list[Any]]]]:
        """工作进度三个 sheet（7e R5，设计 2.5）：PR 进度、单品汇总、周明细。

        按请求区间 + 季节导；能走到这里的都持 ``work_progress:read``，看全部 PR（``PR_SCOPE_ALL``）。
        三份数据与页面同一组 service 方法；单品一次取全部（``limit`` 的 1 ~ 100 是路由的校验，
        service 只切片），「未归属商品」放最后一行；周行写所在周的周一与实际起止。
        """
        service = WorkProgressService(self._s)
        pr_rows = await service.get_for_range(
            tenant_id, *time_range, seasons=seasons, scope=PR_SCOPE_ALL, field_ctx=field_ctx
        )
        goods_page = await service.get_goods(
            tenant_id,
            *time_range,
            seasons=seasons,
            scope=PR_SCOPE_ALL,
            field_ctx=field_ctx,
            limit=_ALL_GOODS,
            offset=0,
        )
        weeks = await service.get_weeks(
            tenant_id, *time_range, seasons=seasons, scope=PR_SCOPE_ALL, field_ctx=field_ctx
        )
        goods_rows = [*goods_page.items]
        if goods_page.unassigned is not None:
            goods_rows.append(goods_page.unassigned)
        return [
            (SHEET_PR, PR_HEADERS, [[r.pr_name, *_metric_cells(r)] for r in pr_rows]),
            (
                SHEET_GOODS,
                GOODS_HEADERS,
                [[r.display_name, *_metric_cells(r)] for r in goods_rows],
            ),
            (
                SHEET_WEEKS,
                WEEK_HEADERS,
                [[r.week_start, r.date_from, r.date_to, *_metric_cells(r)] for r in weeks.items],
            ),
        ]


__all__ = ["ReportExportService", "exportable_report_types"]
