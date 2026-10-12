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


class ReportRangeTooLongError(AppException):
    """下钻等按合作日期取明细的接口：区间跨度超过 366 天（7e R1，报表视图设计 2.1）。"""

    code = "REPORT_RANGE_TOO_LONG"
    status_code = 422
    message = "时间区间跨度不能超过 366 天"


class ReportStyleNotFoundError(AppException):
    code = "REPORT_STYLE_NOT_FOUND"
    status_code = 404
    message = "款式不存在"


class ReportExportTypeInvalidError(AppException):
    code = "REPORT_EXPORT_TYPE_INVALID"
    status_code = 400
    message = "不支持的报表导出类型"


class StoreDailyFutureDateError(AppException):
    """店铺日手填：``day`` 晚于今天（设计 2.4）。"""

    code = "STORE_DAILY_FUTURE_DATE"
    status_code = 422
    message = "不能录入今天之后的日期"


class StoreDailyDateTooEarlyError(AppException):
    """店铺日手填：``day`` 早于 2020-01-01（设计 5.1）。"""

    code = "STORE_DAILY_DATE_TOO_EARLY"
    status_code = 422
    message = "不能录入 2020-01-01 之前的日期"


class SummaryRefreshBusyError(AppException):
    """同一租户已有一次汇总刷新在进行中。

    两次刷新的区间重叠时不能并行：READ COMMITTED 下后一个事务的 DELETE 看不到
    前一个刚插入（未提交）的行，INSERT 会撞唯一索引。所以刷新入口拿租户级
    advisory lock，拿不到就直接报忙，而不是排队等 —— 排队会占住 web worker
    或 Celery 槽位（worker 只有 2 个并发，还要跑采集与备份）。
    """

    code = "REPORT_SUMMARY_REFRESH_BUSY"
    status_code = 409
    message = "汇总表正在刷新中，请稍后再试"


__all__ = [
    "ReportExportTypeInvalidError",
    "ReportInvalidTimePresetError",
    "ReportInvalidTimeRangeError",
    "ReportRangeTooLongError",
    "ReportStyleNotFoundError",
    "StoreDailyDateTooEarlyError",
    "StoreDailyFutureDateError",
    "SummaryRefreshBusyError",
]
