"""单品投产列定义（7e R3，设计 2.3）：页面与导出共用一份，列顺序 = 页面顺序。

导出逐列从 :data:`PRODUCTION_COLUMNS` 取表头与单元格（extra 列仍排在最后）。页面列
（``page=True``）的键与表头导出成同目录的 ``production_columns.snapshot.json``：
``tests/unit/test_production_columns.py`` 守「文件 = 常量」，前端
``features/report/productionColumns.test.tsx`` 读同一份文件守键序与列名，只改一边就红
（改了列：``UPDATE_REPORT_SNAPSHOT=1 pytest tests/unit/test_production_columns.py`` 重写文件）。
主图、趋势是页面专有列，不在这里；商品编码、商品名称、渠道、千牛链接数只进导出
（页面把名称、渠道标签、「N 条链接」都画在「商品」一列里）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.modules.report.advanced_schemas import Recent7Range

ColumnKind = Literal["text", "codes", "money", "count", "rate", "ratio"]


@dataclass(frozen=True, slots=True)
class ProductionColumn:
    key: str  # ProductionRow 的字段名
    header: str
    kind: ColumnKind
    page: bool = True  # 页面上有这一列
    margin: bool = False  # 毛利三列：只给管理员（页面不渲染、导出单元格为空）
    group: Literal["recent7"] | None = None  # 近 7 天列组：表头前缀「近 7 天（MM-DD ~ MM-DD）」


PRODUCTION_COLUMNS: tuple[ProductionColumn, ...] = (
    ProductionColumn("goods_code", "商品编码", "text", page=False),
    ProductionColumn("goods_short_name", "商品简称", "text"),
    ProductionColumn("goods_title", "商品名称", "text", page=False),
    ProductionColumn("channel", "渠道", "text", page=False),
    ProductionColumn("link_count", "千牛链接数", "count", page=False),
    ProductionColumn("style_codes", "含款号", "codes"),
    ProductionColumn("pay_amount", "支付金额", "money"),
    ProductionColumn("pay_orders", "支付件数（不扣刷单）", "count"),
    ProductionColumn("refund_amount", "退款金额", "money"),
    ProductionColumn("return_rate", "退货退款率", "rate"),
    ProductionColumn("confirmed_amount", "实际销售金额", "money"),
    ProductionColumn("promo_cost", "站外推广费", "money"),
    ProductionColumn("ad_spend", "站内投放", "money"),
    ProductionColumn("total_spend", "推广总花费", "money"),
    ProductionColumn("pre_return_roi", "退货前投产比", "ratio"),
    ProductionColumn("net_roi", "净投产比", "ratio"),
    ProductionColumn("unit_deal_cost", "推广单件成交成本", "money"),
    ProductionColumn("add_cart_count", "总加购数", "count"),
    ProductionColumn("add_cart_cost", "总加购成本", "money"),
    ProductionColumn("ad_add_cart_count", "站内加购", "count"),
    ProductionColumn("ad_add_cart_cost", "站内加购成本", "money"),
    ProductionColumn("offsite_add_cart", "站外加购", "count"),
    ProductionColumn("recent7_pay_amount", "支付金额", "money", group="recent7"),
    ProductionColumn("recent7_total_spend", "推广总花费", "money", group="recent7"),
    ProductionColumn("recent7_net_roi", "净投产比", "ratio", group="recent7"),
    ProductionColumn("goods_cost_total", "销售货品总成本", "money", margin=True),
    ProductionColumn("gross_profit", "单品毛利", "money", margin=True),
    ProductionColumn("gross_margin_rate", "单品利润率", "rate", margin=True),
)

PRODUCTION_PAGE_KEYS: tuple[str, ...] = tuple(c.key for c in PRODUCTION_COLUMNS if c.page)


def export_header(col: ProductionColumn, recent7_range: Recent7Range | None) -> str:
    """导出表头：近 7 天列组带区间（同页面列组表头）；没给区间时只写「近 7 天」。"""
    if col.group != "recent7":
        return col.header
    if recent7_range is None:
        return f"近 7 天{col.header}"
    return f"近 7 天（{recent7_range.date_from:%m-%d} ~ {recent7_range.date_to:%m-%d}）{col.header}"


__all__ = [
    "PRODUCTION_COLUMNS",
    "PRODUCTION_PAGE_KEYS",
    "ColumnKind",
    "ProductionColumn",
    "export_header",
]
