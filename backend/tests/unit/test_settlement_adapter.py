"""U06e SettlementImportAdapter 单元测试（parse_row + validate，纯函数无 DB）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.importer.adapters.settlement import (
    SettlementImportAdapter,
    _to_date,
    _to_decimal,
)


def _adapter() -> SettlementImportAdapter:
    return SettlementImportAdapter()


# ---------------------------------------------------------------------------
# _to_date
# ---------------------------------------------------------------------------


def test_to_date_valid():
    assert _to_date("2026-06-01") == date(2026, 6, 1)


def test_to_date_empty():
    assert _to_date("") is None
    assert _to_date(None) is None


def test_to_date_invalid_keeps_raw():
    assert _to_date("2026/06/01") == "2026/06/01"
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
        "推广编号": "AB000001",
        "结算日期": "2026-06-01",
        "金额": "500.00",
        "总金额": "1,500.00",
        "付款金额": "1500.00",
        "付款日期": "2026-06-10",
        "结算状态": "待付款",
        "笔记标题": "夏季新款",
        "备注": "首笔",
    }
    parsed = _adapter().parse_row(row, None)
    assert parsed["promotion_internal_code"] == "AB000001"
    assert parsed["settlement_date"] == date(2026, 6, 1)
    assert parsed["amount"] == Decimal("500.00")
    assert parsed["total_amount"] == Decimal("1500.00")
    assert parsed["payment_amount"] == Decimal("1500.00")
    assert parsed["payment_date"] == date(2026, 6, 10)
    assert parsed["settlement_status"] == "待付款"
    assert parsed["note_title"] == "夏季新款"


def test_parse_row_optional_empty():
    row = {"推广编号": "AB000001", "结算日期": "2026-06-01", "金额": "10", "总金额": "10"}
    parsed = _adapter().parse_row(row, None)
    assert parsed["payment_amount"] is None
    assert parsed["payment_date"] is None
    assert parsed["settlement_status"] is None
    assert parsed["remark"] is None


class _FakeMapping:
    def __init__(self, columns):
        self.mapping_config = {"columns": columns}


def test_parse_row_custom_mapping():
    mapping = _FakeMapping(
        [
            {"source_col": "编号", "target_field": "promotion_internal_code", "type": "str"},
            {"source_col": "日期", "target_field": "settlement_date", "type": "date"},
            {"source_col": "金额", "target_field": "amount", "type": "decimal"},
            {"source_col": "合计", "target_field": "total_amount", "type": "decimal"},
        ]
    )
    parsed = _adapter().parse_row(
        {"编号": "X9", "日期": "2026-01-01", "金额": "100", "合计": "200"}, mapping
    )
    assert parsed["promotion_internal_code"] == "X9"
    assert parsed["settlement_date"] == date(2026, 1, 1)
    assert parsed["amount"] == Decimal("100")
    assert parsed["total_amount"] == Decimal("200")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _valid() -> dict:
    return {
        "promotion_internal_code": "AB000001",
        "settlement_date": date(2026, 6, 1),
        "amount": Decimal("500.00"),
        "total_amount": Decimal("1500.00"),
    }


def test_validate_pass():
    assert _adapter().validate(_valid()) == []


def test_validate_pass_with_optionals():
    p = _valid()
    p["payment_amount"] = Decimal("1500.00")
    p["payment_date"] = date(2026, 6, 10)
    p["settlement_status"] = "已付款"
    assert _adapter().validate(p) == []


def test_validate_missing_promotion_code():
    p = _valid()
    p["promotion_internal_code"] = None
    assert any("推广编号" in e for e in _adapter().validate(p))


def test_validate_missing_amount():
    p = _valid()
    p["amount"] = None
    assert any("金额不能为空" in e for e in _adapter().validate(p))


def test_validate_missing_total_amount():
    p = _valid()
    p["total_amount"] = None
    assert any("总金额不能为空" in e for e in _adapter().validate(p))


def test_validate_negative_amount():
    p = _valid()
    p["amount"] = Decimal("-1")
    assert any("金额必须为非负" in e for e in _adapter().validate(p))


def test_validate_invalid_amount_kept_raw():
    p = _valid()
    p["amount"] = "abc"  # parse_row 留下的非法原串
    assert any("金额必须为非负" in e for e in _adapter().validate(p))


def test_validate_negative_payment_amount():
    p = _valid()
    p["payment_amount"] = Decimal("-5")
    assert any("付款金额必须为非负" in e for e in _adapter().validate(p))


def test_validate_missing_settlement_date():
    p = _valid()
    p["settlement_date"] = None
    assert any("结算日期不能为空" in e for e in _adapter().validate(p))


def test_validate_invalid_settlement_date():
    p = _valid()
    p["settlement_date"] = "2026/06/01"
    assert any("结算日期格式错误" in e for e in _adapter().validate(p))


def test_validate_invalid_payment_date():
    p = _valid()
    p["payment_date"] = "bad"
    assert any("付款日期格式错误" in e for e in _adapter().validate(p))


def test_validate_invalid_status():
    p = _valid()
    p["settlement_status"] = "未知状态"
    assert any("结算状态必须为" in e for e in _adapter().validate(p))


def test_validate_all_five_status_values():
    for status in ["待核查", "待付款", "待财务付款", "已付款", "已驳回"]:
        p = _valid()
        p["settlement_status"] = status
        assert _adapter().validate(p) == []


def test_validate_note_title_length():
    p = _valid()
    p["note_title"] = "X" * 256
    assert any("note_title" in e and "长度" in e for e in _adapter().validate(p))
