"""U16 拍单/刷单导入适配器：exclude_from_roi 的三态解析与按单据类型取默认值。

回归一个生产缺陷：适配器原先用 ``bool(parsed.get("exclude_from_roi"))``，
Excel 没填「是否剔除ROI」列时得到 False，于是所有导入的刷单标记都是 false，
投产报表的「剔除刷单」子查询一条都匹配不到 —— 刷单金额全额计入销售额，投产比虚高。
"""

from __future__ import annotations

import pytest

from app.modules.importer.adapters.order_adjustment import (
    OrderAdjustmentImportAdapter,
    _to_bool,
)


class TestTristateBool:
    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "y", "是", "剔除", "排除", "需剔除"])
    def test_true_tokens(self, raw: str) -> None:
        assert _to_bool(raw) is True

    @pytest.mark.parametrize("raw", ["0", "false", "n", "否", "不剔除", "保留"])
    def test_false_tokens(self, raw: str) -> None:
        assert _to_bool(raw) is False

    @pytest.mark.parametrize("raw", [None, "", "   ", "说不清"])
    def test_unfilled_or_unrecognized_is_none(self, raw: str | None) -> None:
        """未填与无法识别都返回 None，交给调用方按单据类型取默认值。"""
        assert _to_bool(raw) is None


class TestDefaultExcludeFromRoi:
    def test_brushing_defaults_to_excluded(self) -> None:
        adapter = OrderAdjustmentImportAdapter("manual_brush_order", "刷单")
        assert adapter.default_exclude_from_roi is True

    def test_store_order_defaults_to_included(self) -> None:
        """拍单是真实的店铺下单，该计入销售额，不能默认剔除。"""
        adapter = OrderAdjustmentImportAdapter("manual_tao_order", "拍单")
        assert adapter.default_exclude_from_roi is False


class TestParseRow:
    def _parse(self, order_type: str, row: dict[str, object]) -> dict[str, object]:
        source = "manual_brush_order" if order_type == "刷单" else "manual_tao_order"
        return OrderAdjustmentImportAdapter(source, order_type).parse_row(row, None)

    def test_column_absent_parses_to_none(self) -> None:
        parsed = self._parse("刷单", {"金额": "100", "日期": "2026-09-01"})
        assert parsed["exclude_from_roi"] is None

    def test_explicit_no_is_preserved(self) -> None:
        """明确填「否」要被尊重，不能被刷单的默认值覆盖。"""
        parsed = self._parse("刷单", {"金额": "100", "是否剔除ROI": "否"})
        assert parsed["exclude_from_roi"] is False

    def test_alias_column_recognized(self) -> None:
        parsed = self._parse("刷单", {"金额": "100", "ROI剔除": "是"})
        assert parsed["exclude_from_roi"] is True
