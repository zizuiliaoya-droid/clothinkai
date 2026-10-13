"""店铺日 / 周 / 月列定义（7e R6 S11，R4 评审 L5）：页面与导出共用一份，列顺序 = 页面顺序。

导出类型 ``store`` 逐列从 :data:`STORE_COLUMNS` 取表头与单元格，周期列在最前、extra 列排在最后。
列的键与表头导出成同目录的 ``store_columns.snapshot.json``：``tests/unit/test_store_columns.py``
守「文件 = 常量」，前端 ``features/report/store/storeColumns.test.ts`` 读同一份文件守键序与列名，
只改一边就红（改了列：``UPDATE_REPORT_SNAPSHOT=1 pytest tests/unit/test_store_columns.py`` 重写文件）。
周期列不在常量里：页面一列（日「日期」/ 周月「周期」标签），导出日写「日期」、周 / 月写「实际起」「实际止」
（= 行上的实际起止，不是桶首日）；页面的「操作」列也不在这里。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.modules.report.advanced_schemas import StoreReportRow

StoreColumnKind = Literal["count", "money", "rate", "ratio"]
StoreExportGranularity = Literal["day", "week", "month"]


@dataclass(frozen=True, slots=True)
class StoreColumn:
    key: str  # StoreReportRow 的字段名
    header: str
    kind: StoreColumnKind


STORE_COLUMNS: tuple[StoreColumn, ...] = (
    StoreColumn("visitors", "商品访客数合计", "count"),
    StoreColumn("pay_amount", "支付金额", "money"),
    StoreColumn("pay_orders", "支付件数", "count"),
    StoreColumn("refund_amount", "退款金额", "money"),
    StoreColumn("brushing_amount", "刷单本金", "money"),
    StoreColumn("actual_sales", "实际销售额", "money"),
    StoreColumn("ad_spend", "万相台", "money"),
    StoreColumn("ad_spend_total", "全站推消耗", "money"),
    StoreColumn("zhitongche_spend", "直通车消耗", "money"),
    StoreColumn("yinli_spend", "引力魔方消耗", "money"),
    StoreColumn("internal_spend", "站内推广费", "money"),
    StoreColumn("promo_cost", "站外推广成本", "money"),
    StoreColumn("total_spend", "推广总额", "money"),
    StoreColumn("total_spend_ratio", "推广总占比", "rate"),
    StoreColumn("internal_ratio", "站内占比", "rate"),
    StoreColumn("external_ratio", "站外占比", "rate"),
    StoreColumn("net_spend_ratio", "净推广占比", "rate"),
    StoreColumn("net_roi", "净投产", "ratio"),
    StoreColumn("roi_ex_brushing", "投产（扣除刷单）", "ratio"),
    StoreColumn("refund_only_amount", "仅退款", "money"),
    StoreColumn("refund_only_rate", "仅退款率", "rate"),
    StoreColumn("live_pay_amount", "其中直播支付", "money"),
    StoreColumn("live_ad_spend", "其中直播站内花费", "money"),
)

STORE_KEYS: tuple[str, ...] = tuple(c.key for c in STORE_COLUMNS)

# 导出的周期列：日一列「日期」；周 / 月两列实际起止（桶 ∩ 请求区间）
STORE_PERIOD_HEADERS: dict[StoreExportGranularity, tuple[str, ...]] = {
    "day": ("日期",),
    "week": ("实际起", "实际止"),
    "month": ("实际起", "实际止"),
}


def store_period_cells(row: StoreReportRow, granularity: StoreExportGranularity) -> list[date]:
    """导出行的周期单元格，与 :data:`STORE_PERIOD_HEADERS` 一一对应。"""
    if granularity == "day":
        return [row.date_from]
    return [row.date_from, row.date_to]


__all__ = [
    "STORE_COLUMNS",
    "STORE_KEYS",
    "STORE_PERIOD_HEADERS",
    "StoreColumn",
    "StoreColumnKind",
    "StoreExportGranularity",
    "store_period_cells",
]
