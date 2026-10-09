"""8b §6.4：导入数值解析 parse_cn_number（灰豚导出的 w / 万 / 亿、千分位、占位符）。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.importer.cn_numbers import parse_cn_number


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1.2w", Decimal("12000")),
        ("1.2W", Decimal("12000")),
        ("3.5万", Decimal("35000")),
        ("1亿", Decimal("100000000")),
        ("2.35亿", Decimal("235000000")),
        (".5万", Decimal("5000")),
        ("-1,234", Decimal("-1234")),
        ("1，234，567", Decimal("1234567")),
        ("+3", Decimal("3")),
        ("12.50", Decimal("12.50")),
        ("1\xa0234", Decimal("1234")),  # NBSP 当空白
        ("\u3000 5 万 ", Decimal("50000")),  # 全角空格、首尾空白
        (5, Decimal("5")),
        (1.5, Decimal("1.5")),
        (Decimal("7.25"), Decimal("7.25")),
    ],
)
def test_parse(raw: object, expected: Decimal) -> None:
    assert parse_cn_number(raw) == expected


def test_unit_multiplies_exactly() -> None:
    # 不经 float：1.23w 恰好是 12300
    assert parse_cn_number("1.23w") == Decimal("12300")
    assert parse_cn_number("0.0001万") == Decimal("1")


@pytest.mark.parametrize("raw", [None, "", "  ", "-", "--", "—", "——", " -- "])
def test_placeholder_is_none(raw: object) -> None:
    assert parse_cn_number(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "1e5",
        "1E5",
        "1.2w+",
        "12k",
        "5%",
        "1-2万",
        "1~2w",
        "abc",
        "万",
        "1.2.3",
        "1w万",
        ",",
        True,
        False,
        float("nan"),
        float("inf"),
        object(),
    ],
)
def test_invalid_raises(raw: object) -> None:
    with pytest.raises(ValueError, match="^数值格式不正确$"):
        parse_cn_number(raw)


def test_error_message_has_no_value() -> None:
    with pytest.raises(ValueError) as exc_info:
        parse_cn_number("图文500")
    assert "500" not in str(exc_info.value)
    assert "图文" not in str(exc_info.value)
