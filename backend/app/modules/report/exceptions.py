"""U08 report 模块业务异常。"""

from __future__ import annotations

from app.core.exceptions import AppException


class ReportInvalidTimePresetError(AppException):
    code = "REPORT_INVALID_TIME_PRESET"
    status_code = 422
    message = "未知时间筛选预设（last_7d/last_30d/this_month/last_month/custom）"


class ReportInvalidTimeRangeError(AppException):
    code = "REPORT_INVALID_TIME_RANGE"
    status_code = 422
    message = "自定义时间范围非法（需 date_from ≤ date_to 且跨度 ≤ 366 天）"


class ReportStyleNotFoundError(AppException):
    code = "REPORT_STYLE_NOT_FOUND"
    status_code = 404
    message = "款式不存在"


class ReportExportTypeInvalidError(AppException):
    code = "REPORT_EXPORT_TYPE_INVALID"
    status_code = 400
    message = "不支持的报表导出类型"


__all__ = [
    "ReportExportTypeInvalidError",
    "ReportInvalidTimePresetError",
    "ReportInvalidTimeRangeError",
    "ReportStyleNotFoundError",
]
