"""单品投产列定义（7e R3，设计 2.3「页面与导出共用一份列定义」）。

``PRODUCTION_COLUMNS`` 是导出的唯一来源；前端 ``features/report/productionColumns.ts`` 的
``PRODUCTION_PAGE_KEYS`` 照 :data:`_PAGE_KEYS` 写，两边任一改动都要同步改这里。
"""

from __future__ import annotations

from datetime import date

from app.modules.report.advanced_schemas import ProductionRow, Recent7Range
from app.modules.report.production_columns import (
    PRODUCTION_COLUMNS,
    PRODUCTION_PAGE_KEYS,
    export_header,
)

# 页面上按顺序出现的列（主图 / 趋势是页面专有、不在元组里）；与前端 PRODUCTION_PAGE_KEYS 逐项相同
_PAGE_KEYS = (
    "goods_short_name",
    "style_codes",
    "pay_amount",
    "pay_orders",
    "refund_amount",
    "return_rate",
    "confirmed_amount",
    "promo_cost",
    "ad_spend",
    "total_spend",
    "pre_return_roi",
    "net_roi",
    "unit_deal_cost",
    "add_cart_count",
    "add_cart_cost",
    "ad_add_cart_count",
    "ad_add_cart_cost",
    "offsite_add_cart",
    "recent7_pay_amount",
    "recent7_total_spend",
    "recent7_net_roi",
    "goods_cost_total",
    "gross_profit",
    "gross_margin_rate",
)


def test_keys_are_row_fields_and_unique() -> None:
    keys = [c.key for c in PRODUCTION_COLUMNS]
    assert len(keys) == len(set(keys))
    assert set(keys) <= set(ProductionRow.model_fields)
    headers = [c.header for c in PRODUCTION_COLUMNS if c.group is None]
    assert len(headers) == len(set(headers))


def test_page_keys_constant() -> None:
    assert PRODUCTION_PAGE_KEYS == _PAGE_KEYS
    assert tuple(c.key for c in PRODUCTION_COLUMNS if c.page) == _PAGE_KEYS


def test_every_metric_column_listed() -> None:
    """ProductionRow 的字段除了内部用的 id / 图片 / is_suit / extra，都要进列定义（防漏列）。"""
    internal = {"goods_id", "main_image_url", "is_suit", "extra"}
    assert set(ProductionRow.model_fields) - internal == {c.key for c in PRODUCTION_COLUMNS}


def test_margin_and_recent7_groups() -> None:
    margin = [c.key for c in PRODUCTION_COLUMNS if c.margin]
    assert margin == ["goods_cost_total", "gross_profit", "gross_margin_rate"]
    recent7 = [c.key for c in PRODUCTION_COLUMNS if c.group == "recent7"]
    assert recent7 == ["recent7_pay_amount", "recent7_total_spend", "recent7_net_roi"]
    # 改名：待确认收货金额 → 实际销售金额
    by_key = {c.key: c for c in PRODUCTION_COLUMNS}
    assert by_key["confirmed_amount"].header == "实际销售金额"


def test_export_header() -> None:
    by_key = {c.key: c for c in PRODUCTION_COLUMNS}
    rng = Recent7Range(date_from=date(2026, 10, 5), date_to=date(2026, 10, 11))
    assert export_header(by_key["recent7_pay_amount"], rng) == "近 7 天（10-05 ~ 10-11）支付金额"
    assert export_header(by_key["recent7_net_roi"], None) == "近 7 天净投产比"
    assert export_header(by_key["pay_amount"], rng) == "支付金额"
