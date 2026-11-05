"""作品数值、标识与实际模板解析契约。"""

import io
from datetime import timedelta
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.core.security.permissions import EffectivePermissions
from app.modules.blogger.work_models import BloggerWorkSnapshot
from app.modules.blogger.work_schemas import WorkInput
from app.modules.blogger.work_service import work_item
from app.modules.importer import access
from app.modules.importer.adapters.blogger_work import BloggerWorkImportAdapter
from app.modules.importer.domain import validate_mapping_config
from app.modules.importer.models import FieldMapping
from app.modules.importer.templates import build_import_template
from app.tasks.import_tasks import _parse_xlsx

pytestmark = pytest.mark.unit


def payload(**kw):
    return WorkInput.model_validate(
        dict(
            work_id="123",
            published_on="2026-11-01",
            collected_at="2026-11-05 14:30:00",
            source="人工采集",
            **kw,
        )
    )


def test_platform_numbers_null_and_zero_and_ratios():
    data = payload(like_count="14.3w", comment_count="0", share_count="", collect_count="143")
    assert data.like_count == 143000 and data.share_count is None
    assert data.collected_at.utcoffset() == timedelta(hours=8)
    row = BloggerWorkSnapshot(**data.model_dump(), id=uuid4(), platform="抖音")
    item = work_item(row)
    assert item.interaction_total is None
    assert item.collect_like_ratio == 0.001
    assert item.comment_like_ratio == 0
    assert item.share_like_ratio is None
    row.like_count = 0
    row.share_count = 0
    item = work_item(row)
    assert item.interaction_total == 143
    assert item.collect_like_ratio is None


@pytest.mark.parametrize("value", ["-1", "1.1", "oops", str(2**63), True])
def test_invalid_counts(value):
    with pytest.raises(ValidationError):
        payload(like_count=value)


def test_stable_url_preserves_query_and_id_priority():
    values = payload().model_dump()
    values.update(work_id=None, work_url="HTTPS://Example.COM/video/1?item=2#share")
    url = WorkInput(**values)
    assert url.work_url == "https://example.com/video/1?item=2"
    assert (
        WorkInput(**{**values, "work_url": "https://example.com/video/1?item=3"}).work_key
        != url.work_key
    )
    assert WorkInput(**{**values, "work_id": "123"}).work_key == payload().work_key
    assert url.work_key != payload().work_key
    for changes in (
        {"work_url": None},
        {"work_url": "javascript:alert(1)"},
        {"collected_at": "2026-11-05"},
    ):
        with pytest.raises(ValidationError):
            WorkInput(**{**values, **changes})


def test_empty_and_custom_mapped_template_roundtrip():
    adapter = BloggerWorkImportAdapter()
    columns = adapter.builtin_columns()
    columns[0]["source_col"] = "实际平台"
    config = validate_mapping_config(columns, targets=adapter.mapping_targets)
    mapping = FieldMapping(version=2, mapping_config=config)
    data = build_import_template(adapter.source, mapping)
    assert _parse_xlsx(data) == []
    wb = load_workbook(io.BytesIO(data))
    assert wb.active.max_row == 1
    assert wb.active.cell(1, 1).value == "实际平台"
    source = {
        "实际平台": "抖音",
        "账号": "acct",
        "作品ID": "123",
        "发布日期": "2026-11-01",
        "采集时间": "2026-11-05 14:30:00",
        "采集来源": "人工采集",
        "点赞": "14.3w",
    }
    wb.active.append([source.get(cell.value, "") for cell in wb.active[1]])
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    parsed = adapter.parse_row(_parse_xlsx(output.getvalue())[0][1], mapping)
    assert adapter.validate(parsed) == []
    assert adapter.payload(parsed) == payload(like_count="14.3w")
    parsed["work_id"] = None
    assert adapter.validate(parsed)


def test_source_permissions_preserve_deny_and_source_read_gate():
    perms = EffectivePermissions(
        str(uuid4()), frozenset({"blogger:write", "blogger:read"}), frozenset()
    )
    assert access.can_write(perms, "manual_blogger_work")
    assert not access.can_view(perms, "manual_blogger_work")
    denied = EffectivePermissions(str(uuid4()), frozenset({"*"}), frozenset({"blogger:write"}))
    assert not access.can_write(denied, "manual_blogger_work")
