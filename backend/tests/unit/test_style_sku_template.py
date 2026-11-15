"""商品资料模板复用现有 XLSX 解析及颜色/尺码映射规则。"""

import io

import pytest
from openpyxl import load_workbook

from app.modules.importer.adapters.style_sku import StyleSkuImportAdapter
from app.modules.importer.exceptions import ImportMappingInvalidError
from app.modules.importer.models import FieldMapping
from app.modules.importer.templates import build_import_template
from app.tasks.import_tasks import _parse_xlsx


@pytest.mark.parametrize("layout", ["builtin", "combined", "split"])
def test_style_sku_template_roundtrip_preserves_text_codes(layout: str) -> None:
    mapping = None
    if layout != "builtin":
        columns = [
            {"source_col": name, "target_field": field, "type": "str"}
            for name, field in [
                ("货号", "style_code"),
                ("品名", "style_name"),
                ("规格编码", "sku_code"),
            ]
        ]
        columns.extend(
            [{"source_col": "组合规格", "target_field": "color_size", "type": "str"}]
            if layout == "combined"
            else [
                {"source_col": "色", "target_field": "color", "type": "str"},
                {"source_col": "码", "target_field": "size", "type": "str"},
            ]
        )
        mapping = FieldMapping(version=3, mapping_config={"columns": columns})
    else:
        columns = StyleSkuImportAdapter().builtin_columns()
    raw = build_import_template("manual_style_sku", mapping)
    assert _parse_xlsx(raw) == []
    workbook = load_workbook(io.BytesIO(raw))
    sheet = workbook.active
    assert [c.value for c in sheet[1]] == [c["source_col"] for c in columns]
    values = {
        "style_code": "000123",
        "sku_code": "00123456789012345678",
        "style_name": "测试款",
        "color": "深灰",
        "size": "L",
        "color_size": "深灰;L",
        "cost_price": "30.50",
    }
    for index, column in enumerate(columns, 1):
        if column["target_field"] in {"style_code", "sku_code"}:
            assert sheet.column_dimensions[sheet.cell(1, index).column_letter].number_format == "@"
    sheet.append([values.get(c["target_field"], "") for c in columns])
    output = io.BytesIO()
    workbook.save(output)
    workbook.close()
    row_number, row = _parse_xlsx(output.getvalue())[0]
    assert row_number == 1
    adapter = StyleSkuImportAdapter()
    parsed = adapter.parse_row(row, mapping)
    assert adapter.validate(parsed) == []
    assert parsed["style_code"] == "000123"
    assert parsed["sku_code"] == "00123456789012345678"
    assert (parsed["color"], parsed["size"]) == ("深灰", "L")


def test_template_rejects_mapping_without_color_and_size() -> None:
    columns = [
        c
        for c in StyleSkuImportAdapter().builtin_columns()
        if c["target_field"] not in {"color_size", "size"}
    ]
    with pytest.raises(ImportMappingInvalidError, match="颜色.*规格"):
        build_import_template(
            "manual_style_sku", FieldMapping(version=1, mapping_config={"columns": columns})
        )
