"""1105 空模板：真实解析回读、映射和来源权限。"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.core.exceptions import PermissionDeniedError, ResourceNotFoundError
from app.core.security.permissions import EffectivePermissions
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.adapters.promotion import PromotionImportAdapter
from app.modules.importer.exceptions import ImportMappingInvalidError
from app.modules.importer.field_mapping_service import FieldMappingService
from app.modules.importer.models import FieldMapping
from app.modules.importer.templates import build_import_template
from app.tasks.import_tasks import _parse_xlsx

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("source", ["manual_blogger", "manual_promotion"])
def test_empty_template_has_no_importable_rows(source: str) -> None:
    data = build_import_template(source)
    workbook = load_workbook(io.BytesIO(data))
    sheet = workbook.active
    assert sheet.title == "填报数据"
    assert sheet.max_row == 1
    assert sheet.freeze_panes == "A2"
    assert "填写说明与示例" in workbook.sheetnames
    assert sheet.data_validations.count == 1
    assert _parse_xlsx(data) == []
    adapter = BloggerImportAdapter() if source == "manual_blogger" else PromotionImportAdapter()
    assert [cell.value for cell in sheet[1]] == [c["source_col"] for c in adapter.builtin_columns()]
    workbook.close()


@pytest.mark.parametrize(
    ("source", "row"),
    [
        (
            "manual_blogger",
            {"账号": "douyin_001", "昵称": "测试博主", "平台": "抖音", "报价": "300.50"},
        ),
        (
            "manual_promotion",
            {
                "款式编码": "STYLE01",
                "小红书ID": "dy_001",
                "平台": "抖音",
                "报价金额": "300.50",
                "合作日期": "2026-11-05",
            },
        ),
    ],
)
def test_filled_template_roundtrips_through_real_parser(source: str, row: dict[str, str]) -> None:
    workbook = load_workbook(io.BytesIO(build_import_template(source)))
    sheet = workbook.active
    sheet.append([row.get(cell.value, "") for cell in sheet[1]])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    parsed_rows = _parse_xlsx(output.getvalue())
    assert len(parsed_rows) == 1
    adapter = BloggerImportAdapter() if source == "manual_blogger" else PromotionImportAdapter()
    parsed = adapter.parse_row(parsed_rows[0][1], None)
    assert adapter.validate(parsed) == []
    assert parsed["platform"] == "抖音"


def test_active_mapping_primary_headers_are_used_without_alias_columns() -> None:
    mapping = FieldMapping(
        version=4,
        mapping_config={
            "columns": [
                {
                    "source_col": "我的账号",
                    "target_field": "xiaohongshu_id",
                    "type": "str",
                    "aliases": ["账号旧称"],
                },
                {"source_col": "我的昵称", "target_field": "nickname", "type": "str"},
            ]
        },
    )
    workbook = load_workbook(io.BytesIO(build_import_template("manual_blogger", mapping)))
    assert [c.value for c in workbook.active[1]] == ["我的账号", "我的昵称"]
    workbook.active.append(["account_1", "昵称1"])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    parsed = BloggerImportAdapter().parse_row(_parse_xlsx(output.getvalue())[0][1], mapping)
    assert BloggerImportAdapter().validate(parsed) == []


def test_invalid_mapping_does_not_download_an_unusable_template() -> None:
    mapping = FieldMapping(
        version=1,
        mapping_config={
            "columns": [
                {"source_col": "昵称", "target_field": "nickname", "type": "str"},
            ]
        },
    )
    with pytest.raises(ImportMappingInvalidError, match="必填"):
        build_import_template("manual_blogger", mapping)
    with pytest.raises(ResourceNotFoundError):
        build_import_template("huitun_douyin")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "scopes", "revokes", "allowed"),
    [
        ("manual_blogger", {"importer.batch:write"}, set(), True),
        ("manual_blogger_audience", {"blogger:write"}, set(), True),
        ("manual_blogger_audience", {"importer.batch:write"}, set(), False),
        ("manual_blogger_audience", {"*"}, {"blogger:write"}, False),
        ("manual_promotion", {"importer.batch:write"}, set(), False),
        ("manual_promotion", {"promotion_create:write"}, set(), True),
        ("manual_blogger", {"importer.batch:read"}, set(), False),
        ("manual_blogger", {"*"}, {"importer.batch:write"}, False),
    ],
)
async def test_download_uses_source_upload_permission_and_current_tenant(
    source: str, scopes: set[str], revokes: set[str], allowed: bool
) -> None:
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    service = FieldMappingService(AsyncMock())
    service._repo = SimpleNamespace(get_active=AsyncMock(return_value=None))
    perms = EffectivePermissions(str(user.id), frozenset(scopes), frozenset(revokes))
    if allowed:
        data = await service.download_template(source, user, perms)
        assert data.startswith(b"PK")
        service._repo.get_active.assert_awaited_once_with(user.tenant_id, source)
    else:
        with pytest.raises(PermissionDeniedError):
            await service.download_template(source, user, perms)
        service._repo.get_active.assert_not_awaited()
