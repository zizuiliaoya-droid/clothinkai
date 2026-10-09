"""8b manual_blogger 的解析 / 校验与 applier 新字段（设计 §6.2、§6.4、§6.6，评审 N2 / N7 / N17）。纯函数。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.modules.blogger.enums import Platform
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.conflict_appliers import (
    EXTERNAL_IMAGE_REASON,
    ApplierValueError,
    BloggerApplier,
)

pytestmark = pytest.mark.unit

_ADAPTER = BloggerImportAdapter()
_APPLIER = BloggerApplier()


def _parse(row: dict[str, Any]) -> dict[str, Any]:
    return _ADAPTER.parse_row(row, None)


def _valid(**kw: Any) -> dict[str, Any]:
    return {"xiaohongshu_id": "acc001", "nickname": "小美", **kw}


# ---------------------------------------------------------------------------
# 表头与别名（§6.2）
# ---------------------------------------------------------------------------


def test_new_headers() -> None:
    parsed = _parse(
        {
            "账号": " acc.01 ",
            "昵称": "小美",
            "平台": " 抖音 ",
            "微信": "wx1",
            "粉丝数": "1.2w",
            "类目标签": "美妆;护肤",
            "报价": "1,299.50",
            "主页链接": " https://example.com/u/1 ",
            "网页ID": " 61900000001 ",
            "多余列": "x",
        }
    )
    assert parsed["xiaohongshu_id"] == "acc.01"
    assert parsed["platform"] == "抖音"
    assert parsed["follower_count"] == 12000
    assert parsed["quote"] == Decimal("1299.50")
    assert parsed["category_tags"] == ["美妆", "护肤"]
    assert parsed["homepage_url"] == "https://example.com/u/1"
    assert parsed["web_id"] == "61900000001"
    assert "多余列" not in parsed


@pytest.mark.parametrize(
    ("row", "field", "expected"),
    [
        ({"小红书ID": "a1"}, "xiaohongshu_id", "a1"),
        ({"小红书号": "a2"}, "xiaohongshu_id", "a2"),
        ({"账号": "-", "小红书号": "a3"}, "xiaohongshu_id", "a3"),  # 主列占位符 → 读别名
        ({"小红书昵称": "昵称甲"}, "nickname", "昵称甲"),
        ({"微信号": "wx2"}, "wechat", "wx2"),
        ({"粉丝量": "3.5万"}, "follower_count", 35000),
        ({"标签": "穿搭"}, "category_tags", ["穿搭"]),
    ],
)
def test_aliases(row: dict[str, Any], field: str, expected: Any) -> None:
    assert _parse(row)[field] == expected


def test_aliases_masked_with_builtin_columns() -> None:
    """失败明细按内置映射遮挡：别名列（微信号）同样受保护。"""
    cols = {c["target_field"]: c for c in _ADAPTER.builtin_columns()}
    assert "微信号" in cols["wechat"]["aliases"]
    assert "quality_tags" not in cols  # 模版去掉「质量标签」


# ---------------------------------------------------------------------------
# 数值（§6.4）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12,500", 12500),
        ("1.2w", 12000),
        ("1.23456w", 12346),  # 带单位的小数 ROUND_HALF_UP 取整
        ("1亿", 100_000_000),
        ("--", None),
        (12500.0, 12500),  # xlsx 数字格
        ("12.5", "12.5"),  # 不带单位的小数不是合法粉丝数，原串留给 validate
        ("1.2w+", "1.2w+"),
    ],
)
def test_follower_parse(raw: Any, expected: Any) -> None:
    assert _parse({"粉丝数": raw})["follower_count"] == expected


def test_follower_validate() -> None:
    assert any("粉丝数" in e for e in _ADAPTER.validate(_valid(follower_count="1.2w+")))
    assert any("粉丝数" in e for e in _ADAPTER.validate(_valid(follower_count=2**31)))
    assert _ADAPTER.validate(_valid(follower_count=2**31 - 1)) == []


def test_quote_parse_and_validate() -> None:
    assert _parse({"报价": "1w"})["quote"] == Decimal("10000")
    assert _parse({"报价": "图文500"})["quote"] == "图文500"
    assert any("报价" in e for e in _ADAPTER.validate(_valid(quote="图文500")))
    errs = _ADAPTER.validate(_valid(quote=Decimal("100000000")))
    assert errs == ["报价必须为非负数字且小于 1 亿"]


# ---------------------------------------------------------------------------
# 平台与账号（§6.2，N7）
# ---------------------------------------------------------------------------


def test_platform_enum() -> None:
    for p in Platform:
        assert _ADAPTER.validate(_valid(platform=p.value)) == []
    assert _ADAPTER.validate(_valid(platform=None)) == []  # 空 → 小红书
    [err] = _ADAPTER.validate(_valid(platform="微博"))
    assert err == f"平台必须为 {'/'.join(p.value for p in Platform)} 之一"


@pytest.mark.parametrize("account", ["a b", "账号1", "a/b", "a@b"])
def test_account_pattern(account: str) -> None:
    assert "账号只能包含字母、数字、_ . -" in _ADAPTER.validate(_valid(xiaohongshu_id=account))


@pytest.mark.parametrize("account", ["G66G77", "a.b_c-1", "12345"])
def test_account_pattern_ok(account: str) -> None:
    assert _ADAPTER.validate(_valid(xiaohongshu_id=account)) == []


def test_web_id_length() -> None:
    assert any("web_id" in e for e in _ADAPTER.validate(_valid(web_id="1" * 65)))


# ---------------------------------------------------------------------------
# 质量标签只读（D5，r2 N17）
# ---------------------------------------------------------------------------


def test_quality_tags_present() -> None:
    assert _parse({"质量标签": "优质"})["quality_tags_present"] is True
    assert "quality_tags_present" not in _parse({"质量标签": "-"})
    assert "quality_tags_present" not in _parse({"质量标签": ""})
    assert "quality_tags_present" not in _parse({})


class _FakeMapping:
    def __init__(self, columns: list[dict[str, Any]]) -> None:
        self.mapping_config = {"columns": columns}


def test_quality_tags_custom_mapping() -> None:
    """自定义映射把某列指到 quality_tags：不取值，只记「文件给了」。"""
    mapping = _FakeMapping(
        [
            {"source_col": "号", "target_field": "xiaohongshu_id", "type": "str"},
            {"source_col": "评级", "target_field": "quality_tags", "type": "list"},
        ]
    )
    parsed = _ADAPTER.parse_row({"号": "a1", "评级": "优质"}, mapping)  # type: ignore[arg-type]
    assert parsed["xiaohongshu_id"] == "a1"
    assert "quality_tags" not in parsed
    assert parsed["quality_tags_present"] is True


# ---------------------------------------------------------------------------
# 比较字段（§6.2）
# ---------------------------------------------------------------------------


def test_compare_specs_exclude_key_like_fields() -> None:
    names = {s.name for s in _ADAPTER.compare_specs()}
    assert {"platform", "quality_tags", "quote_note"}.isdisjoint(names)
    assert {"web_id", "homepage_url"} <= names
    # 冲突页按字段筛选：旧冲突里的平台 / 质量标签还能筛出来批量保留
    assert {"platform", "quality_tags", "web_id", "homepage_url"} <= _ADAPTER.compare_field_names()
    assert "quote_note" not in _ADAPTER.compare_field_names()


# ---------------------------------------------------------------------------
# applier 新字段
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("platform", "抖音", "平台是判重键，不能经导入修改"),
        ("quality_tags", ["优质"], "系统标签只读"),
        ("web_id", "1" * 65, "超过 64 字"),
        ("homepage_url", "ftp://example.com", EXTERNAL_IMAGE_REASON),
        ("homepage_url", "https://exa mple.com", EXTERNAL_IMAGE_REASON),
        ("quote_note", "x" * 501, "超过 500 字"),
    ],
)
def test_applier_invalid(field: str, value: Any, reason: str) -> None:
    with pytest.raises(ApplierValueError) as exc_info:
        _APPLIER.check(field, value)
    assert (exc_info.value.field, exc_info.value.reason) == (field, reason)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("web_id", " 61900000001 ", "61900000001"),
        ("homepage_url", " https://example.com/u/1 ", "https://example.com/u/1"),
        ("quote_note", "图文500", "图文500"),
    ],
)
def test_applier_valid(field: str, value: Any, expected: Any) -> None:
    assert _APPLIER.check(field, value) == expected


def test_quote_note_sensitive_like_quote() -> None:
    specs = {s.name: s for s in BloggerApplier.specs}
    assert specs["quote_note"].sensitive == specs["quote"].sensitive == ("blogger", "quote")
