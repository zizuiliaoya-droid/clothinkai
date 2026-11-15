"""1115：真实旧租户映射的多平台模板回读及旧表头兼容。"""

import copy
import io
from decimal import Decimal

import pytest
from openpyxl import load_workbook

from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.models import FieldMapping
from app.modules.importer.templates import build_import_template
from app.tasks.import_tasks import _parse_csv, _parse_xlsx


def legacy_mapping() -> FieldMapping:
    # 生产旧映射的结构，不含任何业务记录。
    return FieldMapping(
        version=1,
        mapping_config={
            "columns": [
                {"source_col": label, "target_field": field, "type": kind}
                for label, field, kind in [
                    ("小红书ID", "xiaohongshu_id", "str"),
                    ("小红书昵称", "nickname", "str"),
                    ("微信号", "wechat", "str"),
                    ("粉丝量", "follower_count", "int"),
                    ("报价", "quote", "decimal"),
                    ("粉丝画像", "gender_target", "str"),
                    ("联系人", "remark", "str"),
                ]
            ]
        },
    )


@pytest.mark.parametrize("platform", ["抖音", "小红书"])
def test_legacy_mapping_download_roundtrip_preserves_custom_columns(platform: str) -> None:
    mapping = legacy_mapping()
    before = copy.deepcopy(mapping.mapping_config)
    workbook = load_workbook(io.BytesIO(build_import_template("manual_blogger", mapping)))
    sheet = workbook.active
    assert [c.value for c in sheet[1]] == [
        "账号",
        "博主昵称",
        "微信号",
        "粉丝量",
        "报价",
        "粉丝画像",
        "联系人",
        "平台",
    ]
    assert sheet.cell(1, 8).comment.text == "必填"
    assert not sheet.data_validations.dataValidation[0].allow_blank
    sheet.append(
        [
            "same_account",
            "测试昵称",
            "test_wechat",
            "12,500",
            "300.50",
            "女性",
            "自定义备注",
            platform,
        ]
    )
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    rows = _parse_xlsx(output.getvalue())
    assert rows[0][0] == 1
    adapter = BloggerImportAdapter()
    parsed = adapter.parse_row(rows[0][1], mapping)
    assert adapter.validate(parsed) == []
    assert parsed == {
        "xiaohongshu_id": "same_account",
        "nickname": "测试昵称",
        "wechat": "test_wechat",
        "follower_count": 12500,
        "quote": Decimal("300.50"),
        "gender_target": "女性",
        "remark": "自定义备注",
        "platform": platform,
    }
    assert mapping.mapping_config == before


@pytest.mark.parametrize(
    ("platform_column", "platform_cell"),
    [("", ""), (",平台", ""), (",平台", ","), (",平台", ", "), (",平台", ",—"), (",平台", ",微博")],
)
def test_csv_missing_blank_or_invalid_platform_has_row_error(
    platform_column: str, platform_cell: str
) -> None:
    raw = f"小红书ID,小红书昵称{platform_column}\nold_account,旧模板昵称{platform_cell}\n".encode()
    row_number, row = _parse_csv(raw)[0]
    adapter = BloggerImportAdapter()
    errors = adapter.validate(adapter.parse_row(row, legacy_mapping()))
    assert row_number == 1
    assert errors and all("平台" in e for e in errors)


@pytest.mark.parametrize("nickname_header", ["小红书昵称", "小红书博主", "昵称", "博主昵称"])
def test_legacy_headers_with_explicit_platform_remain_usable(nickname_header: str) -> None:
    adapter = BloggerImportAdapter()
    row = {"小红书ID": "legacy_01", nickname_header: "旧昵称", "平台": "抖音"}
    parsed = adapter.parse_row(row, legacy_mapping())
    assert adapter.validate(parsed) == []
    assert parsed["platform"] == "抖音"
    assert parsed["nickname"] == "旧昵称"


def test_custom_identity_aliases_and_canonical_platform_take_priority() -> None:
    mapping = FieldMapping(
        version=2,
        mapping_config={
            "columns": [
                {
                    "source_col": "自定义号",
                    "target_field": "xiaohongshu_id",
                    "type": "str",
                    "aliases": ["原账号"],
                },
                {"source_col": "我的昵称", "target_field": "nickname", "type": "str"},
                {"source_col": "原平台", "target_field": "platform", "type": "str"},
            ]
        },
    )
    adapter = BloggerImportAdapter()
    parsed = adapter.parse_row(
        {"原账号": "alias_01", "我的昵称": "昵称", "平台": "抖音", "原平台": "小红书"}, mapping
    )
    assert adapter.validate(parsed) == []
    assert parsed["platform"] == "抖音"
    assert parsed["xiaohongshu_id"] == "alias_01"
