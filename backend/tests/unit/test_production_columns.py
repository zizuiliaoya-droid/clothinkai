"""单品投产列定义（7e R3，设计 2.3「页面与导出共用一份列定义」）。

``PRODUCTION_COLUMNS`` 是导出的唯一来源。页面列的键与表头导出成
``app/modules/report/production_columns.snapshot.json``（R3 评审 L5）：本文件守「文件 = 常量」，
前端 ``features/report/productionColumns.test.tsx`` 读同一份文件守键序与列名，两个 CI job 各跑一半。
改了列：``UPDATE_REPORT_SNAPSHOT=1 pytest tests/unit/test_production_columns.py`` 重写文件，再同步前端。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from app.modules.report.advanced_schemas import ProductionRow, Recent7Range
from app.modules.report.production_columns import (
    PRODUCTION_COLUMNS,
    PRODUCTION_PAGE_KEYS,
    export_header,
)

SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "modules"
    / "report"
    / "production_columns.snapshot.json"
)


def build_snapshot() -> list[dict[str, Any]]:
    """页面上按顺序出现的列（主图 / 趋势是页面专有、不在常量里）。"""
    return [{"key": c.key, "header": c.header} for c in PRODUCTION_COLUMNS if c.page]


def render(snapshot: list[dict[str, Any]]) -> str:
    return json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"


def test_keys_are_row_fields_and_unique() -> None:
    keys = [c.key for c in PRODUCTION_COLUMNS]
    assert len(keys) == len(set(keys))
    assert set(keys) <= set(ProductionRow.model_fields)
    headers = [c.header for c in PRODUCTION_COLUMNS if c.group is None]
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
    ], "production_columns.snapshot.json 键序与常量不一致：UPDATE_REPORT_SNAPSHOT=1 重写并同步前端"
    assert got == expected, "production_columns.snapshot.json 列名与常量不一致"
    assert tuple(c["key"] for c in got) == PRODUCTION_PAGE_KEYS


def test_snapshot_covers_page_columns() -> None:
    """场景有效性：快照不是空壳——页面 24 列、含近 7 天三列与毛利三列，导出专有列不在里面。"""
    keys = [c["key"] for c in build_snapshot()]
    assert len(keys) == 24
    assert {"pay_orders", "recent7_net_roi", "gross_margin_rate"} <= set(keys)
    assert not {"goods_code", "goods_title", "channel", "link_count"} & set(keys)


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
    # R3 评审 L2：只数启用的千牛链接（页面「N 条链接」不变）
    assert by_key["link_count"].header == "千牛链接数"


def test_export_header() -> None:
    by_key = {c.key: c for c in PRODUCTION_COLUMNS}
    rng = Recent7Range(date_from=date(2026, 10, 5), date_to=date(2026, 10, 11))
    assert export_header(by_key["recent7_pay_amount"], rng) == "近 7 天（10-05 ~ 10-11）支付金额"
    assert export_header(by_key["recent7_net_roi"], None) == "近 7 天净投产比"
    assert export_header(by_key["pay_amount"], rng) == "支付金额"
