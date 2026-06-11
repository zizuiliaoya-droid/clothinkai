"""U06d PromotionImportAdapter 单元测试（parse_row + validate，纯函数无 DB）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.importer.adapters.promotion import (
    PromotionImportAdapter,
    _to_date,
    _to_decimal,
)


def _adapter() -> PromotionImportAdapter:
    return PromotionImportAdapter()


# ---------------------------------------------------------------------------
# _to_date
# ---------------------------------------------------------------------------


def test_to_date_valid():
    assert _to_date("2026-06-01") == date(2026, 6, 1)


def test_to_date_empty():
    assert _to_date("") is None
    assert _to_date(None) is None


def test_to_date_invalid_keeps_raw():
    assert _to_date("2026/06/01") == "2026/06/01"  # 非 ISO → 原串
    assert _to_date("abc") == "abc"


# ---------------------------------------------------------------------------
# _to_decimal
# ---------------------------------------------------------------------------


def test_to_decimal_thousands_no_float():
    result = _to_decimal("1,299.00")
    assert result == Decimal("1299.00")
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)


def test_to_decimal_empty_and_invalid():
    assert _to_decimal("") is None
    assert _to_decimal("abc") == "abc"


# ---------------------------------------------------------------------------
# parse_row
# ---------------------------------------------------------------------------


def test_parse_row_default_mapping():
    row = {
        "款式编码": "ST001",
        "小红书ID": "xhs001",
        "报价金额": "500.00",
        "平台": "小红书",
        "合作日期": "2026-06-01",
        "计划发布日期": "2026-06-05",
    }
    parsed = _adapter().parse_row(row, None)
    assert parsed["style_code"] == "ST001"
    assert parsed["xiaohongshu_id"] == "xhs001"
    assert parsed["quote_amount"] == Decimal("500.00")
    assert parsed["cooperation_date"] == date(2026, 6, 1)
    assert parsed["scheduled_publish_date"] == date(2026, 6, 5)
    assert parsed["sku_code"] is None


class _FakeMapping:
    def __init__(self, columns):
        self.mapping_config = {"columns": columns}


def test_parse_row_custom_mapping():
    mapping = _FakeMapping(
        [
            {"source_col": "货号", "target_field": "style_code", "type": "str"},
            {"source_col": "博主", "target_field": "xiaohongshu_id", "type": "str"},
            {"source_col": "日期", "target_field": "cooperation_date", "type": "date"},
            {"source_col": "金额", "target_field": "quote_amount", "type": "decimal"},
        ]
    )
    parsed = _adapter().parse_row(
        {"货号": "ST9", "博主": "x9", "日期": "2026-01-01", "金额": "100"}, mapping
    )
    assert parsed["style_code"] == "ST9"
    assert parsed["cooperation_date"] == date(2026, 1, 1)
    assert parsed["quote_amount"] == Decimal("100")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _valid() -> dict:
    return {
        "style_code": "ST001",
        "xiaohongshu_id": "xhs001",
        "quote_amount": Decimal("500.00"),
        "platform": "小红书",
        "cooperation_date": date(2026, 6, 1),
    }


def test_validate_pass():
    assert _adapter().validate(_valid()) == []


def test_validate_missing_style_code():
    p = _valid()
    p["style_code"] = None
    assert any("款式编码" in e for e in _adapter().validate(p))


def test_validate_missing_xhs_id():
    p = _valid()
    p["xiaohongshu_id"] = ""
    assert any("小红书ID" in e for e in _adapter().validate(p))


def test_validate_missing_quote():
    p = _valid()
    p["quote_amount"] = None
    assert any("报价金额" in e for e in _adapter().validate(p))


def test_validate_negative_quote():
    p = _valid()
    p["quote_amount"] = Decimal("-1")
    assert any("报价金额必须为非负" in e for e in _adapter().validate(p))


def test_validate_missing_cooperation_date():
    p = _valid()
    p["cooperation_date"] = None
    assert any("合作日期不能为空" in e for e in _adapter().validate(p))


def test_validate_invalid_cooperation_date():
    p = _valid()
    p["cooperation_date"] = "2026/06/01"  # parse_row 留下的非法原串
    assert any("合作日期格式错误" in e for e in _adapter().validate(p))


def test_validate_invalid_scheduled_date():
    p = _valid()
    p["scheduled_publish_date"] = "bad"
    assert any("计划发布日期格式错误" in e for e in _adapter().validate(p))


def test_validate_negative_cost():
    p = _valid()
    p["cost_snapshot"] = Decimal("-5")
    assert any("成本快照" in e for e in _adapter().validate(p))


def test_validate_length_limit():
    p = _valid()
    p["style_code"] = "X" * 65
    assert any("style_code" in e and "长度" in e for e in _adapter().validate(p))
