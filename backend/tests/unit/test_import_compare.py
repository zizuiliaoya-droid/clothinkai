"""8a-6：导入比较口径（设计 §4.3，补充二 Q1 / Q2）。纯函数。"""

from __future__ import annotations

import json
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.importer.compare import (
    FieldSpec,
    ValueKind,
    check_money,
    diff_fields,
    is_placeholder,
    json_value,
    normalize,
)

_SPECS = (
    FieldSpec("nickname", "昵称", ValueKind.TEXT),
    FieldSpec("follower_count", "粉丝数", ValueKind.INT),
    FieldSpec("quote", "报价", ValueKind.DECIMAL, sensitive=("blogger", "quote")),
    FieldSpec("tags", "类目标签", ValueKind.TAGS),
    FieldSpec("name", "商品名称", ValueKind.TEXT, create_only=True),
)


@pytest.mark.unit
class TestPlaceholder:
    @pytest.mark.parametrize("raw", [None, "", "   ", "-", "--", "—", "——", " - "])
    def test_placeholders(self, raw: object) -> None:
        assert is_placeholder(raw) is True

    @pytest.mark.parametrize("raw", ["0", "a-b", "---", 0, Decimal("0"), "无"])
    def test_not_placeholders(self, raw: object) -> None:
        assert is_placeholder(raw) is False


@pytest.mark.unit
class TestNormalize:
    def test_decimal_quantized(self) -> None:
        assert normalize(ValueKind.DECIMAL, "60") == "60.00"
        assert normalize(ValueKind.DECIMAL, Decimal("60.00")) == "60.00"
        assert normalize(ValueKind.DECIMAL, 60) == "60.00"
        assert normalize(ValueKind.DECIMAL, "0.005") == "0.01"  # ROUND_HALF_UP

    def test_thousands_separator(self) -> None:
        assert normalize(ValueKind.DECIMAL, "1,288.00") == "1288.00"
        assert normalize(ValueKind.INT, "12,500") == 12500

    def test_text_strip_and_empty(self) -> None:
        assert normalize(ValueKind.TEXT, "  a  ") == "a"
        assert normalize(ValueKind.TEXT, "   ") is None
        assert normalize(ValueKind.TEXT, None) is None

    def test_tags_as_set(self) -> None:
        assert normalize(ValueKind.TAGS, ["护肤", " 美妆 ", "美妆", ""]) == ["护肤", "美妆"]
        assert normalize(ValueKind.TAGS, []) is None
        assert normalize(ValueKind.TAGS, ["", " "]) is None

    def test_ref_to_str(self) -> None:
        uid = uuid4()
        assert normalize(ValueKind.REF, uid) == str(uid)
        assert normalize(ValueKind.REF, str(uid)) == str(uid)

    def test_blank_non_text_is_empty(self) -> None:
        assert normalize(ValueKind.DECIMAL, " ") is None
        assert normalize(ValueKind.INT, "") is None

    def test_idempotent(self) -> None:
        for kind, value in (
            (ValueKind.DECIMAL, "60"),
            (ValueKind.TAGS, ["b", "a"]),
            (ValueKind.INT, "7"),
            (ValueKind.TEXT, " x "),
        ):
            once = normalize(kind, value)
            assert normalize(kind, once) == once


@pytest.mark.unit
class TestJsonValue:
    def test_types(self) -> None:
        uid = uuid4()
        assert json_value(ValueKind.DECIMAL, Decimal("60")) == "60.00"
        assert json_value(ValueKind.REF, uid) == str(uid)
        assert json_value(ValueKind.INT, "12") == 12
        assert json_value(ValueKind.TAGS, ["b", "a"]) == ["a", "b"]
        assert json_value(ValueKind.TEXT, "  原样  ") == "  原样  "  # 审计记实际值，不去空白
        assert json_value(ValueKind.TEXT, None) is None
        json.dumps([json_value(ValueKind.DECIMAL, Decimal("1.5")), json_value(ValueKind.REF, uid)])


@pytest.mark.unit
class TestDiffFields:
    def test_fill_conflict_same(self) -> None:
        system = {"nickname": "小美", "follower_count": None, "quote": "60.00", "tags": ["a"]}
        incoming = {"nickname": "小美", "follower_count": 100, "quote": "65", "tags": ["a"]}
        result = diff_fields(_SPECS, system, incoming, fill_empty=True)
        assert result.fills == {"follower_count": 100}
        assert [d.field for d in result.conflicts] == ["quote"]
        diff = result.conflicts[0]
        assert (diff.system, diff.file) == ("60.00", "65.00")
        assert diff.sensitive == ("blogger", "quote")
        assert diff.to_json()["sensitive"] == ["blogger", "quote"]
        assert result.compared == frozenset({"nickname", "follower_count", "quote", "tags"})

    def test_60_equals_60_00(self) -> None:
        result = diff_fields(_SPECS, {"quote": "60.00"}, {"quote": Decimal("60")}, fill_empty=True)
        assert result.conflicts == []
        assert result.fills == {}
        assert result.compared == frozenset({"quote"})

    def test_tags_compared_as_set(self) -> None:
        result = diff_fields(
            _SPECS, {"tags": ["美妆", "护肤"]}, {"tags": ["护肤", "美妆", "美妆"]}, fill_empty=True
        )
        assert result.conflicts == []

    def test_empty_file_value_not_compared(self) -> None:
        result = diff_fields(
            _SPECS,
            {"nickname": "小美", "quote": "60.00", "tags": ["a"]},
            {"nickname": "  ", "quote": None, "tags": []},
            fill_empty=True,
        )
        assert result.compared == frozenset()
        assert result.conflicts == []
        assert result.fills == {}

    def test_missing_key_not_compared(self) -> None:
        result = diff_fields(_SPECS, {"quote": "60.00"}, {}, fill_empty=True)
        assert result.compared == frozenset()

    def test_create_only_not_compared(self) -> None:
        result = diff_fields(_SPECS, {"name": "旧款名"}, {"name": "新款名"}, fill_empty=True)
        assert result.compared == frozenset()
        assert result.conflicts == []

    def test_fill_empty_false_means_conflict(self) -> None:
        result = diff_fields(_SPECS, {"quote": None}, {"quote": "65"}, fill_empty=False)
        assert result.fills == {}
        assert [(d.field, d.system, d.file) for d in result.conflicts] == [("quote", None, "65.00")]

    def test_system_empty_string_is_empty(self) -> None:
        result = diff_fields(_SPECS, {"nickname": "  "}, {"nickname": "小美"}, fill_empty=True)
        assert result.fills == {"nickname": "小美"}

    def test_existing_placeholder_is_a_value(self) -> None:
        """系统里已有的「-」按普通值比较：文件给了真实值时进冲突，不当空自动补。"""
        result = diff_fields(_SPECS, {"nickname": "-"}, {"nickname": "小美"}, fill_empty=True)
        assert result.fills == {}
        assert [d.field for d in result.conflicts] == ["nickname"]

    def test_displays(self) -> None:
        result = diff_fields(
            _SPECS,
            {"tags": ["a", "b"]},
            {"tags": ["c"]},
            fill_empty=True,
        )
        assert result.conflicts[0].system_display == "a、b"
        assert result.conflicts[0].file_display == "c"
        ref = FieldSpec("brand_id", "品牌", ValueKind.REF)
        a, b = uuid4(), uuid4()
        result = diff_fields(
            (ref,),
            {"brand_id": a},
            {"brand_id": b},
            fill_empty=True,
            displays={"brand_id": ("品牌甲", "品牌乙")},
        )
        assert (result.conflicts[0].system_display, result.conflicts[0].file_display) == (
            "品牌甲",
            "品牌乙",
        )


@pytest.mark.unit
class TestCheckMoney:
    def test_bounds(self) -> None:
        with pytest.raises(ValueError, match="必须为非负数字且小于 1 亿"):
            check_money("-0.01")
        assert check_money("99999999.99") == Decimal("99999999.99")
        with pytest.raises(ValueError):
            check_money("100000000")
        with pytest.raises(ValueError):
            check_money("99999999.995")  # 量化后进位到 1 亿
        assert check_money(60) == Decimal("60.00")
        assert check_money("1,288.5") == Decimal("1288.50")

    @pytest.mark.parametrize("bad", ["abc", "NaN", "Infinity", True, None, "1e30"])
    def test_invalid(self, bad: object) -> None:
        with pytest.raises(ValueError) as exc_info:
            check_money(bad)
        assert str(bad) not in str(exc_info.value) or bad is None
