"""店铺日 / 周 / 月列定义（7e R6 S11，R4 评审 L5：导出跟页面列走）。

``STORE_COLUMNS`` 是导出类型 ``store`` 的唯一来源。列的键与表头导出成
``app/modules/report/store_columns.snapshot.json``：本文件守「文件 = 常量」，前端
``features/report/store/storeColumns.test.ts`` 读同一份文件守键序与列名，两个 CI job 各跑一半。
改了列：``UPDATE_REPORT_SNAPSHOT=1 pytest tests/unit/test_store_columns.py`` 重写文件，再同步前端。
"""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.modules.report.advanced_schemas import StoreReportRow
from app.modules.report.store_columns import (
    STORE_COLUMNS,
    STORE_KEYS,
    STORE_PERIOD_HEADERS,
    store_period_cells,
)

SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "modules"
    / "report"
    / "store_columns.snapshot.json"
)


def build_snapshot() -> list[dict[str, Any]]:
    return [{"key": c.key, "header": c.header} for c in STORE_COLUMNS]


def render(snapshot: list[dict[str, Any]]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"


def test_keys_are_row_fields_and_unique() -> None:
    assert len(STORE_KEYS) == len(set(STORE_KEYS))
    assert set(STORE_KEYS) <= set(StoreReportRow.model_fields)
    headers = [c.header for c in STORE_COLUMNS]
    assert len(headers) == len(set(headers))


def test_snapshot_matches_constants() -> None:
    expected = build_snapshot()
    if os.environ.get("UPDATE_REPORT_SNAPSHOT") == "1":
        SNAPSHOT.write_text(render(expected), encoding="utf-8", newline="\n")
    assert SNAPSHOT.exists(), f"缺 {SNAPSHOT}：UPDATE_REPORT_SNAPSHOT=1 跑本测试生成"
    got = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    # 顺序也是契约（= 页面列顺序）：先比键序，失败信息更好读
    assert [c["key"] for c in got] == [
        c["key"] for c in expected
    ], "store_columns.snapshot.json 键序与常量不一致：UPDATE_REPORT_SNAPSHOT=1 重写并同步前端"
    assert got == expected, "store_columns.snapshot.json 列名与常量不一致"


def test_every_metric_column_listed() -> None:
    """StoreReportRow 除了周期、「已录 x / y 天」两个计数与 extra，都要进列定义（防漏列）。"""
    internal = {"date_from", "date_to", "refund_only_days", "day_count", "extra"}
    assert set(StoreReportRow.model_fields) - internal == set(STORE_KEYS)


def test_renamed_headers() -> None:
    """场景有效性：改过名的列用新名（默 16「商品访客数合计」、「支付件数」），没有旧名。"""
    by_key = {c.key: c.header for c in STORE_COLUMNS}
    assert by_key["visitors"] == "商品访客数合计"
    assert by_key["pay_orders"] == "支付件数"
    headers = set(by_key.values())
    assert not {"访客数", "支付订单", "支付订单数", "支付额"} & headers
    assert len(STORE_COLUMNS) == 23


def _row(date_from: date, date_to: date) -> StoreReportRow:
    zero = Decimal("0")
    return StoreReportRow(
        date_from=date_from,
        date_to=date_to,
        visitors=0,
        pay_amount=zero,
        pay_orders=0,
        refund_amount=zero,
        brushing_amount=zero,
        actual_sales=zero,
        ad_spend=zero,
        internal_spend=zero,
        promo_cost=zero,
        total_spend=zero,
        live_pay_amount=zero,
        live_ad_spend=zero,
        refund_only_days=0,
        day_count=1,
    )


def test_period_cells() -> None:
    """日一列日期；周 / 月两列实际起止（周三起的周写周三，不写周一）。"""
    assert STORE_PERIOD_HEADERS == {
        "day": ("日期",),
        "week": ("实际起", "实际止"),
        "month": ("实际起", "实际止"),
    }
    day = date(2026, 9, 2)
    assert store_period_cells(_row(day, day), "day") == [day]
    wed, sun = date(2026, 9, 2), date(2026, 9, 6)
    assert store_period_cells(_row(wed, sun), "week") == [wed, sun]
    mid, end = date(2026, 9, 14), date(2026, 9, 30)
    assert store_period_cells(_row(mid, end), "month") == [mid, end]
    for granularity, headers in STORE_PERIOD_HEADERS.items():
        assert len(store_period_cells(_row(wed, sun), granularity)) == len(headers)
