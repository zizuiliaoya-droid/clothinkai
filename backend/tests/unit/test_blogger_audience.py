"""画像业务口径、格式与真实空模板回读。"""

import io
from datetime import date
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from app.core.security.permissions import EffectivePermissions
from app.modules.blogger.audience_schemas import (
    AudienceInput,
    audience_updated_on,
    parse_percentage,
)
from app.modules.importer import access
from app.modules.importer.adapters.blogger_audience import (
    BloggerAudienceImportAdapter,
    excel_percent_value,
)
from app.modules.importer.domain import validate_mapping_config
from app.modules.importer.exceptions import RowValidationError
from app.modules.importer.models import FieldMapping
from app.modules.importer.templates import build_import_template
from app.tasks.import_tasks import _parse_rows

pytestmark = pytest.mark.unit


def payload(**changes):
    return AudienceInput.model_validate(
        {
            "dimension": "age",
            "collected_on": "2026-11-05",
            "source": "来源截图",
            "completeness": "partial",
            "estimated": False,
            "entries": [
                {"label": "41–50", "percentage": "23.5%"},
                {"label": "50+", "percentage": 0},
            ],
            **changes,
        }
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [("23.5%", 23.5), (23.5, 23.5), ("0.235", 0.235), ("0%", 0), ("", None), (None, None)],
)
def test_percentage_unit_and_missing(value, expected):
    assert parse_percentage(value) == expected


@pytest.mark.parametrize("value", [-1, "100.1%", "NaN", "inf", True, "1/2"])
def test_percentage_rejects_invalid(value):
    with pytest.raises(ValueError):
        parse_percentage(value)


@pytest.mark.parametrize("total", [99, 99.1, 100, 101])
def test_complete_inclusive_tolerance(total):
    result = payload(
        completeness="complete",
        entries=[{"label": "甲", "percentage": 49}, {"label": "乙", "percentage": total - 49}],
    )
    assert result.completeness == "complete"


@pytest.mark.parametrize("total", [98.99, 101.01])
def test_complete_outside_tolerance(total):
    with pytest.raises(ValidationError, match="99%～101%"):
        payload(
            completeness="complete",
            entries=[{"label": "甲", "percentage": 49}, {"label": "乙", "percentage": total - 49}],
        )


def test_raw_age_labels_partial_no_normalization_and_tgi_provenance():
    result = payload()
    assert [(e.label, e.percentage) for e in result.entries] == [("41–50", 23.5), ("50+", 0)]
    with pytest.raises(ValidationError, match="实际来源"):
        payload(entries=[{"label": "41–50", "tgi": 120}])
    result = payload(
        tgi_source="原图：平台整体用户为基准", entries=[{"label": "41–50", "tgi": 120}]
    )
    assert result.entries[0].percentage is None
    with pytest.raises(ValidationError, match="每个分类"):
        payload(
            completeness="complete", tgi_source="原图", entries=[{"label": "41–50", "tgi": 120}]
        )
    with pytest.raises(ValidationError, match="城市级别"):
        payload(dimension="city_tier", entries=[{"label": "山东", "percentage": 10}])
    with pytest.raises(ValidationError, match="城市层级"):
        payload(dimension="province", entries=[{"label": "新一线", "percentage": 10}])


def test_excel_percentage_only_explicit_native_format():
    assert excel_percent_value(0.235, "0.0%") == "23.500%"
    assert excel_percent_value(0.235, "General") == 0.235
    assert excel_percent_value(0.235, '0.0"%"') == 0.235
    assert excel_percent_value(0.235, r"0.0\%") == 0.235
    assert excel_percent_value("23.5%", "0%") == "23.5%"


def test_empty_template_mapping_native_percent_and_group_roundtrip():
    adapter = BloggerAudienceImportAdapter()
    columns = adapter.builtin_columns()
    columns[0]["source_col"] = "我的平台"
    mapping = FieldMapping(
        version=2, mapping_config=validate_mapping_config(columns, targets=adapter.mapping_targets)
    )
    data = build_import_template(adapter.source, mapping)
    assert _parse_rows(data, "template.xlsx", percent_formats=True) == []
    wb = load_workbook(io.BytesIO(data))
    sheet = wb.active
    assert sheet.max_row == 1
    row = {
        "我的平台": "抖音",
        "账号": "acct",
        "维度": "年龄",
        "原始标签": "41–50",
        "占比": 0.235,
        "采集日期": "2026-11-05",
        "采集来源": "来源截图",
        "完整性": "部分",
        "是否估计": "否",
    }
    sheet.append([row.get(c.value, "") for c in sheet[1]])
    percent_col = next(c.column for c in sheet[1] if c.value == "占比")
    sheet.cell(2, percent_col).number_format = "0.0%"
    row.update({"原始标签": "50+", "占比": 0})
    sheet.append([row.get(c.value, "") for c in sheet[1]])
    output = io.BytesIO()
    wb.save(output)
    wb.close()
    rows = _parse_rows(output.getvalue(), "template.xlsx", percent_formats=True)
    assert rows[0][1]["占比"].endswith("%")
    parsed = [(n, adapter.parse_row(raw, mapping)) for n, raw in rows]
    assert adapter.payload(parsed) == payload()
    assert _parse_rows(output.getvalue(), "template.xlsx")[0][1]["占比"] == "0.235"
    parsed[1][1]["percentage"] = "bad"
    with pytest.raises(RowValidationError, match="第 2 数据行"):
        adapter.payload(parsed)


def test_group_metadata_mismatch_and_source_permissions():
    adapter = BloggerAudienceImportAdapter()
    row = {
        "platform": "抖音",
        "xiaohongshu_id": "acct",
        **payload().model_dump(exclude={"entries"}),
        "label": "41–50",
        "percentage": 23.5,
    }
    with pytest.raises(RowValidationError, match="第 8 数据行"):
        adapter.payload([(3, row), (8, {**row, "label": "50+", "source": "另一来源"})])
    perms = EffectivePermissions(
        str(uuid4()), frozenset({"blogger:write", "blogger:read"}), frozenset()
    )
    assert access.can_write(perms, adapter.source) and not access.can_view(perms, adapter.source)
    assert not access.can_write(
        EffectivePermissions(str(uuid4()), frozenset({"*"}), frozenset({"blogger:write"})),
        adapter.source,
    )
    assert audience_updated_on({"note_stats": {"avg_likes": 0}}) is None
    assert audience_updated_on(
        {"structured_v1": {"dimensions": {"age": {"collected_on": "2026-11-05"}}}}
    ) == date(2026, 11, 5)


def test_migration_074_upgrade_downgrade_sql_and_chain():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    from alembic import op
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from app.core.db import Base

    root = Path(__file__).parents[2] / "alembic"
    scripts = ScriptDirectory(str(root))
    assert scripts.get_heads() == ["074_blogger_audience_captures"]
    assert (
        scripts.get_revision("074_blogger_audience_captures").down_revision
        == "073_style_optional_drafts"
    )
    spec = spec_from_file_location(
        "audience_migration", root / "versions/074_blogger_audience_captures.py"
    )
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql",
        opts={"as_sql": True, "output_buffer": output, "target_metadata": Base.metadata},
    )
    module.op = op
    with Operations.context(context):
        module.upgrade()
        module.downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE blogger_audience_capture" in sql
    assert "CREATE UNIQUE INDEX uq_blogger_audience_capture" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql and "WITH CHECK" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in sql
    assert "audience_profile - 'structured_v1'" in sql
    assert "DROP TABLE blogger_audience_capture" in sql
