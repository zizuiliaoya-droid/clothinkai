"""真实中文列经过adapter，保留自定义映射语义。"""

from types import SimpleNamespace

from app.modules.importer.adapters.promotion import PromotionImportAdapter


def raw(category="单篇"):
    return {
        "款式编码": "ST1",
        "小红书ID": "x1",
        "报价金额": "1,234.50",
        "合作日期": "2026-10-20",
        "合作形式": category,
        "多余列": "不入库",
    }


def test_default_content_mapping_and_length():
    adapter = PromotionImportAdapter()
    parsed = adapter.parse_row(raw(), None)
    assert parsed["content_category"] == "单篇"
    assert not adapter.validate(parsed)
    assert "多余列" not in parsed
    assert adapter.parse_row(raw(" "), None)["content_category"] == ""
    assert any(
        "content_category" in error
        for error in adapter.validate(adapter.parse_row(raw("一二三四五六七八九"), None))
    )


def test_custom_mapping_does_not_silently_gain_default_columns():
    adapter = PromotionImportAdapter()
    config = {"columns": [{"source_col": "款式编码", "target_field": "style_code", "type": "str"}]}
    mapping = SimpleNamespace(mapping_config=config)
    assert adapter.parse_row(raw(), mapping) == {"style_code": "ST1"}
    assert len(config["columns"]) == 1
