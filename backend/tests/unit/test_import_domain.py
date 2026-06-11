"""U06a domain 层单元测试（纯函数，无 DB）。

覆盖：csv_safe（CSV injection 防护）+ compute_sha256（流式）+ safe_filename（防穿越）
+ validate_mapping_config（映射校验）。
"""

from __future__ import annotations

import io

import pytest

from app.modules.importer.domain import (
    compute_sha256,
    csv_safe,
    safe_filename,
    validate_mapping_config,
)
from app.modules.importer.exceptions import ImportMappingInvalidError


# ---------------------------------------------------------------------------
# csv_safe（CSV injection 防护）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("=SUM(A1:A2)", "'=SUM(A1:A2)"),
        ("+1234", "'+1234"),
        ("-1+2", "'-1+2"),
        ("@cmd", "'@cmd"),
        ("normal", "normal"),
        ("", ""),
        (None, ""),
        (123, "123"),
    ],
)
def test_csv_safe_escapes_dangerous_prefix(value, expected):
    assert csv_safe(value) == expected


def test_csv_safe_only_prefixes_first_char():
    # 仅首字符危险才转义；中间出现不转义
    assert csv_safe("a=b") == "a=b"


# ---------------------------------------------------------------------------
# compute_sha256
# ---------------------------------------------------------------------------


def test_compute_sha256_known_value():
    # echo -n "hello" | sha256sum
    expected = (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    digest, size = compute_sha256(io.BytesIO(b"hello"))
    assert digest == expected
    assert size == 5


def test_compute_sha256_chunked_equals_whole():
    data = b"x" * 100000
    d1, s1 = compute_sha256(io.BytesIO(data), chunk_size=7)
    d2, s2 = compute_sha256(io.BytesIO(data), chunk_size=65536)
    assert d1 == d2
    assert s1 == s2 == 100000


# ---------------------------------------------------------------------------
# safe_filename（防 R2 key 穿越）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "../../etc/passwd",
        "a/b\\c.csv",
        "normal.csv",
    ],
)
def test_safe_filename_strips_separators(raw):
    out = safe_filename(raw)
    # 核心安全属性：无路径分隔符（无 / 或 \），无法穿越目录
    assert "/" not in out
    assert "\\" not in out


def test_safe_filename_empty_defaults():
    assert safe_filename(None) == "upload"
    assert safe_filename("") == "upload"


def test_safe_filename_preserves_chinese():
    assert "订单" in safe_filename("订单导入.csv")


# ---------------------------------------------------------------------------
# validate_mapping_config
# ---------------------------------------------------------------------------


def test_validate_mapping_config_ok():
    cfg = validate_mapping_config(
        [
            {"source_col": "名称", "target_field": "name", "type": "str"},
            {
                "source_col": "日期",
                "target_field": "date",
                "type": "date",
                "transform": "%Y-%m-%d",
            },
        ]
    )
    assert cfg["columns"][0]["target_field"] == "name"
    assert cfg["columns"][1]["transform"] == "%Y-%m-%d"


def test_validate_mapping_config_empty_raises():
    with pytest.raises(ImportMappingInvalidError):
        validate_mapping_config([])


def test_validate_mapping_config_bad_type_raises():
    with pytest.raises(ImportMappingInvalidError):
        validate_mapping_config(
            [{"source_col": "a", "target_field": "b", "type": "weird"}]
        )


def test_validate_mapping_config_date_requires_transform():
    with pytest.raises(ImportMappingInvalidError):
        validate_mapping_config(
            [{"source_col": "a", "target_field": "b", "type": "date"}]
        )


def test_validate_mapping_config_duplicate_target_raises():
    with pytest.raises(ImportMappingInvalidError):
        validate_mapping_config(
            [
                {"source_col": "a", "target_field": "x"},
                {"source_col": "b", "target_field": "x"},
            ]
        )
