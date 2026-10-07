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
    # 旧表头（款式名称 / 尺码 / SKU编码）作为别名照常读到
    assert (parsed["style_name"], parsed["size"]) == ("连衣裙", "M")
    # 8a-4：类目不再读
    assert "category" not in parsed


def test_parse_row_placeholders_are_not_given():
    """FR-6.5：空、-、--、—、—— 当未提供（不是格式错）；主列是占位符时再试别名。"""
    row = {
        "款式编码": "-",
        "货号": "ST7",
        "商品编码": "SK7",
        "商品名称": "裙",
        "颜色": "红",
        "规格": "M",
        "基本售价": "--",
        "成本价": "—",
        "采购价": "——",
        "市场|吊牌价": " 1,288.00 ",
        "商品简称": "-",
        "图片": "",
    }
    parsed = _adapter().parse_row(row, None)
    assert parsed["style_code"] == "ST7"
    assert (parsed["base_price"], parsed["cost_price"], parsed["purchase_price"]) == (
        None,
        None,
        None,
    )
    assert parsed["tag_price"] == Decimal("1288.00")
    assert parsed["goods_short_name"] is None
    assert parsed["external_image_url"] is None
    assert _adapter().validate(parsed) == []


def test_parse_row_custom_mapping_ignores_category():
    """旧自定义映射里的 category 被忽略（FR-3.2）；brand_code / season 照常生效。"""
    mapping = _FakeMapping(
        [
            {"source_col": "货号", "target_field": "style_code", "type": "str"},
            {"source_col": "类目", "target_field": "category", "type": "str"},
            {"source_col": "牌子", "target_field": "brand_code", "type": "str"},
            {"source_col": "季", "target_field": "season", "type": "str"},
        ]
    )
    parsed = _adapter().parse_row({"货号": "ST1", "类目": "裙", "牌子": "LN", "季": "夏"}, mapping)
    assert parsed == {"style_code": "ST1", "brand_code": "LN", "season": "夏"}


def test_builtin_columns_from_catalog():
    """内置默认由目录生成：不再有类目，读商品简称与图片。"""
    cols = {c["target_field"]: c for c in _adapter().builtin_columns()}
    assert "category" not in cols
    assert cols["goods_short_name"]["source_col"] == "商品简称"
    assert cols["external_image_url"]["source_col"] == "图片"
    assert cols["style_name"]["source_col"] == "商品名称"
    assert "款式名称" in cols["style_name"]["aliases"]


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
    parsed = _adapter().parse_row({"商品货号": "ST9", "规格编码": "SK9", "成本": "10.5"}, mapping)
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
    # 8a-4：文案改用映射目录的界面名（旧「SKU编码」→「商品编码」）
    assert any("商品编码" in e for e in errs)


def test_validate_each_required_field():
    # 类目下线（8a-3 / 8a-4）：不读、不写，也不再回落「未分类」
    adapter = _adapter()
    for field, label in [
        ("style_code", "款式编码"),
        ("style_name", "商品名称"),
        ("sku_code", "商品编码"),
        ("color", "颜色"),
        ("size", "规格"),
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
    assert errs == ["基本售价必须为非负数字且小于 1 亿"]


def test_validate_money_upper_limit():
    # Numeric(10,2)：量化后 ≥ 1 亿整行失败（原来由数据库报错）
    p = _valid_parsed()
    p["cost_price"] = Decimal("100000000")
    assert _adapter().validate(p) == ["成本价必须为非负数字且小于 1 亿"]
    p["cost_price"] = Decimal("99999999.99")
    assert _adapter().validate(p) == []


def test_validate_bad_sourcing_type():
    p = _valid_parsed()
    p["sourcing_type"] = "进口"
    errs = _adapter().validate(p)
    assert any("货源类型" in e for e in errs)


def test_validate_length_limit():
    p = _valid_parsed()
    p["sku_code"] = "X" * 65
    errs = _adapter().validate(p)
    assert any("商品编码" in e and "长度" in e for e in errs)


def test_validate_sourcing_empty_ok():
    # sourcing_type 空 → 通过（upsert 时默认"自产"）
    p = _valid_parsed()
    p["sourcing_type"] = None
    assert _adapter().validate(p) == []
