"""U06b StyleSkuImportAdapter 单元测试（parse_row + validate，纯函数无 DB）。"""

from __future__ import annotations

from decimal import Decimal

from app.modules.importer.adapters.style_sku import (
    StyleSkuImportAdapter,
    _to_decimal,
)


def _adapter() -> StyleSkuImportAdapter:
    return StyleSkuImportAdapter()


# ---------------------------------------------------------------------------
# _to_decimal
# ---------------------------------------------------------------------------


def test_to_decimal_thousands_separator():
    assert _to_decimal("1,299.00") == Decimal("1299.00")
    assert isinstance(_to_decimal("39.9"), Decimal)


def test_to_decimal_empty_to_none():
    assert _to_decimal("") is None
    assert _to_decimal(None) is None
    assert _to_decimal("   ") is None


def test_to_decimal_invalid_keeps_raw_string():
    # 非法值保留原串（供 validate 检出）
    assert _to_decimal("abc") == "abc"


def test_to_decimal_no_float():
    # 禁 float：结果是 Decimal 不是 float
    result = _to_decimal("0.1")
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)


# ---------------------------------------------------------------------------
# parse_row
# ---------------------------------------------------------------------------


def test_parse_row_default_mapping():
    row = {
        "款式编码": "ST001",
        "款式名称": "连衣裙",
        "类目": "连衣裙",
        "SKU编码": "SK001",
        "颜色": "红",
        "尺码": "M",
        "成本价": "1,299.00",
        "货源类型": "采购",
    }
    parsed = _adapter().parse_row(row, None)
    assert parsed["style_code"] == "ST001"
    assert parsed["sku_code"] == "SK001"
    assert parsed["cost_price"] == Decimal("1299.00")
    assert parsed["sourcing_type"] == "采购"
    assert parsed["season"] is None  # 缺列 → None


def test_parse_row_strips_whitespace():
    parsed = _adapter().parse_row({"款式编码": "  ST001  "}, None)
    assert parsed["style_code"] == "ST001"


class _FakeMapping:
    def __init__(self, columns):
        self.mapping_config = {"columns": columns}


def test_parse_row_custom_mapping():
    mapping = _FakeMapping(
        [
            {"source_col": "商品货号", "target_field": "style_code", "type": "str"},
            {"source_col": "规格编码", "target_field": "sku_code", "type": "str"},
            {"source_col": "成本", "target_field": "cost_price", "type": "decimal"},
        ]
    )
    parsed = _adapter().parse_row(
        {"商品货号": "ST9", "规格编码": "SK9", "成本": "10.5"}, mapping
    )
    assert parsed["style_code"] == "ST9"
    assert parsed["sku_code"] == "SK9"
    assert parsed["cost_price"] == Decimal("10.5")


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _valid_parsed() -> dict:
    return {
        "style_code": "ST001",
        "style_name": "连衣裙",
        "category": "连衣裙",
        "sku_code": "SK001",
        "color": "红",
        "size": "M",
        "cost_price": Decimal("39.90"),
        "sourcing_type": "自产",
    }


def test_validate_pass():
    assert _adapter().validate(_valid_parsed()) == []


def test_validate_missing_required():
    p = _valid_parsed()
    p["sku_code"] = None
    errs = _adapter().validate(p)
    assert any("SKU编码" in e for e in errs)


def test_validate_each_required_field():
    adapter = _adapter()
    for field, label in [
        ("style_code", "款式编码"),
        ("style_name", "款式名称"),
        ("category", "类目"),
        ("sku_code", "SKU编码"),
        ("color", "颜色"),
        ("size", "尺码"),
    ]:
        p = _valid_parsed()
        p[field] = None
        errs = adapter.validate(p)
        assert any(label in e for e in errs), f"{field} 缺失未报错"


def test_validate_negative_decimal():
    p = _valid_parsed()
    p["cost_price"] = Decimal("-1")
    errs = _adapter().validate(p)
    assert any("成本价" in e for e in errs)


def test_validate_non_decimal_raw_string():
    # parse_row 留下的非法原串 → validate 检出
    p = _valid_parsed()
    p["base_price"] = "abc"
    errs = _adapter().validate(p)
    assert any("吊牌价" in e for e in errs)


def test_validate_bad_sourcing_type():
    p = _valid_parsed()
    p["sourcing_type"] = "进口"
    errs = _adapter().validate(p)
    assert any("货源类型" in e for e in errs)


def test_validate_length_limit():
    p = _valid_parsed()
    p["sku_code"] = "X" * 65
    errs = _adapter().validate(p)
    assert any("sku_code" in e and "长度" in e for e in errs)


def test_validate_sourcing_empty_ok():
    # sourcing_type 空 → 通过（upsert 时默认"自产"）
    p = _valid_parsed()
    p["sourcing_type"] = None
    assert _adapter().validate(p) == []
