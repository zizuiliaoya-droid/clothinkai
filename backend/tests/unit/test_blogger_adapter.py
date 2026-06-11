"""U06c BloggerImportAdapter 单元测试（parse_row + validate，纯函数无 DB）。"""

from __future__ import annotations

from decimal import Decimal

from app.modules.importer.adapters.blogger import (
    BloggerImportAdapter,
    _split_tags,
    _to_decimal,
    _to_int,
)


def _adapter() -> BloggerImportAdapter:
    return BloggerImportAdapter()


# ---------------------------------------------------------------------------
# _split_tags
# ---------------------------------------------------------------------------


def test_split_tags_multiple_separators():
    assert _split_tags("美妆;护肤,穿搭；日常，通勤") == [
        "美妆",
        "护肤",
        "穿搭",
        "日常",
        "通勤",
    ]


def test_split_tags_strip_and_dedupe_empty():
    assert _split_tags(" 美妆 ; ; 护肤 ") == ["美妆", "护肤"]


def test_split_tags_empty():
    assert _split_tags(None) == []
    assert _split_tags("") == []
    assert _split_tags("   ") == []


def test_split_tags_single():
    assert _split_tags("美妆") == ["美妆"]


# ---------------------------------------------------------------------------
# _to_int / _to_decimal
# ---------------------------------------------------------------------------


def test_to_int_thousands():
    assert _to_int("12,500") == 12500
    assert _to_int("1000") == 1000


def test_to_int_empty():
    assert _to_int("") is None
    assert _to_int(None) is None


def test_to_int_invalid_keeps_raw():
    assert _to_int("abc") == "abc"
    assert _to_int("12.5") == "12.5"  # float 串非 int → 原串


def test_to_decimal_no_float():
    result = _to_decimal("500.00")
    assert isinstance(result, Decimal)
    assert not isinstance(result, float)


def test_to_decimal_thousands():
    assert _to_decimal("1,299.50") == Decimal("1299.50")


def test_to_decimal_invalid_keeps_raw():
    assert _to_decimal("xyz") == "xyz"


# ---------------------------------------------------------------------------
# parse_row
# ---------------------------------------------------------------------------


def test_parse_row_default_mapping():
    row = {
        "小红书ID": "xhs001",
        "昵称": "小美",
        "粉丝数": "12,500",
        "报价": "500.00",
        "类目标签": "美妆;护肤",
        "质量标签": "优质",
    }
    parsed = _adapter().parse_row(row, None)
    assert parsed["xiaohongshu_id"] == "xhs001"
    assert parsed["nickname"] == "小美"
    assert parsed["follower_count"] == 12500
    assert parsed["quote"] == Decimal("500.00")
    assert parsed["category_tags"] == ["美妆", "护肤"]
    assert parsed["quality_tags"] == ["优质"]
    assert parsed["platform"] is None  # 缺列 → None（upsert 时默认"小红书"）


class _FakeMapping:
    def __init__(self, columns):
        self.mapping_config = {"columns": columns}


def test_parse_row_custom_mapping():
    mapping = _FakeMapping(
        [
            {"source_col": "博主ID", "target_field": "xiaohongshu_id", "type": "str"},
            {"source_col": "粉丝量", "target_field": "follower_count", "type": "int"},
            {"source_col": "标签", "target_field": "category_tags", "type": "list"},
        ]
    )
    parsed = _adapter().parse_row(
        {"博主ID": "x9", "粉丝量": "8000", "标签": "A,B"}, mapping
    )
    assert parsed["xiaohongshu_id"] == "x9"
    assert parsed["follower_count"] == 8000
    assert parsed["category_tags"] == ["A", "B"]


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def _valid() -> dict:
    return {
        "xiaohongshu_id": "xhs001",
        "nickname": "小美",
        "follower_count": 12500,
        "quote": Decimal("500.00"),
        "category_tags": ["美妆"],
    }


def test_validate_pass():
    assert _adapter().validate(_valid()) == []


def test_validate_missing_xhs_id():
    p = _valid()
    p["xiaohongshu_id"] = None
    errs = _adapter().validate(p)
    assert any("小红书ID" in e for e in errs)


def test_validate_missing_nickname():
    p = _valid()
    p["nickname"] = ""
    errs = _adapter().validate(p)
    assert any("昵称" in e for e in errs)


def test_validate_negative_follower():
    p = _valid()
    p["follower_count"] = -1
    errs = _adapter().validate(p)
    assert any("粉丝数" in e for e in errs)


def test_validate_non_int_follower():
    p = _valid()
    p["follower_count"] = "abc"  # parse_row 留下的非法原串
    errs = _adapter().validate(p)
    assert any("粉丝数" in e for e in errs)


def test_validate_negative_quote():
    p = _valid()
    p["quote"] = Decimal("-5")
    errs = _adapter().validate(p)
    assert any("报价" in e for e in errs)


def test_validate_length_limit():
    p = _valid()
    p["xiaohongshu_id"] = "X" * 65
    errs = _adapter().validate(p)
    assert any("xiaohongshu_id" in e and "长度" in e for e in errs)


def test_validate_optional_none_ok():
    # follower/quote/tags 空 → 通过
    p = {"xiaohongshu_id": "x", "nickname": "n"}
    assert _adapter().validate(p) == []
