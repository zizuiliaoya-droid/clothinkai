"""8a-4 映射目录（TargetSpec）：内置默认由目录生成、保存时按目录校验（设计 §4.7、§5.1，AC 40）。"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from app.modules.importer.adapters.style_sku import MAPPING_TARGETS, StyleSkuImportAdapter
from app.modules.importer.domain import (
    TargetSpec,
    builtin_columns_from_targets,
    validate_mapping_config,
)
from app.modules.importer.exceptions import ImportMappingInvalidError


def _cols(*pairs: tuple[str, str], **types: str) -> list[dict[str, Any]]:
    return [{"source_col": s, "target_field": t, "type": types.get(t, "str")} for s, t in pairs]


_MINIMAL = (
    ("款号", "style_code"),
    ("商品编码", "sku_code"),
    ("商品名称", "style_name"),
    ("颜色及规格", "color_size"),
)


class TestCatalog:
    def test_fifteen_targets_and_names(self) -> None:
        """§5.1 的 15 个目标字段；brand_code / season 沿用旧名；类目不在目录里。"""
        fields = [t.field for t in MAPPING_TARGETS]
        assert fields == [
            "style_code",
            "sku_code",
            "style_name",
            "color_size",
            "color",
            "size",
            "base_price",
            "cost_price",
            "purchase_price",
            "tag_price",
            "sourcing_type",
            "goods_short_name",
            "brand_code",
            "season",
            "external_image_url",
        ]
        by = {t.field: t for t in MAPPING_TARGETS}
        assert by["style_name"].create_only is True
        assert {f for f, t in by.items() if t.required} == {"style_code", "sku_code", "style_name"}
        assert by["cost_price"].sensitive == ("sku", "cost_price")
        assert by["purchase_price"].sensitive == ("sku", "purchase_price")

    def test_builtin_columns_generated_from_catalog(self) -> None:
        adapter = StyleSkuImportAdapter()
        cols = adapter.builtin_columns()
        assert cols == builtin_columns_from_targets(MAPPING_TARGETS)
        for col, target in zip(cols, MAPPING_TARGETS, strict=True):
            assert col["source_col"] == target.default_col
            assert col["target_field"] == target.field
            assert col["type"] == target.type
            assert col["aliases"] == list(target.aliases)
        assert adapter.sensitive_targets == {
            "cost_price": ("sku", "cost_price"),
            "purchase_price": ("sku", "purchase_price"),
        }

    def test_builtin_columns_shape_like_old_default(self) -> None:
        spec = TargetSpec("x", "某列", "decimal", "某列", ("别名",))
        assert builtin_columns_from_targets([spec]) == [
            {"source_col": "某列", "target_field": "x", "type": "decimal", "aliases": ["别名"]}
        ]


class TestValidateWithTargets:
    def test_minimal_ok_and_type_from_catalog(self) -> None:
        cfg = validate_mapping_config(
            _cols(*_MINIMAL, ("进价", "cost_price"), cost_price="str"), targets=MAPPING_TARGETS
        )
        by = {c["target_field"]: c for c in cfg["columns"]}
        assert by["cost_price"]["type"] == "decimal"  # 客户端传 str 被忽略
        assert by["style_code"]["required"] is True
        assert "aliases" not in by["style_code"]

    def test_unknown_target_category(self) -> None:
        with pytest.raises(ImportMappingInvalidError, match="不认识的目标字段 category"):
            validate_mapping_config(_cols(*_MINIMAL, ("分类", "category")), targets=MAPPING_TARGETS)

    def test_missing_style_code(self) -> None:
        with pytest.raises(ImportMappingInvalidError, match="款式编码"):
            validate_mapping_config(_cols(*_MINIMAL[1:]), targets=MAPPING_TARGETS)

    @pytest.mark.parametrize(
        ("color_cols", "ok"),
        [
            ((("颜色及规格", "color_size"),), True),
            ((("颜色", "color"), ("规格", "size")), True),
            ((("颜色", "color"),), False),
            ((), False),
        ],
    )
    def test_color_group(self, color_cols: tuple[tuple[str, str], ...], ok: bool) -> None:
        cols = _cols(*_MINIMAL[:3], *color_cols)
        if ok:
            validate_mapping_config(cols, targets=MAPPING_TARGETS)
        else:
            with pytest.raises(ImportMappingInvalidError, match="至少要映射一组"):
                validate_mapping_config(cols, targets=MAPPING_TARGETS)

    def test_one_column_per_target(self) -> None:
        with pytest.raises(ImportMappingInvalidError, match="重复"):
            validate_mapping_config(
                _cols(*_MINIMAL, ("另一个款号", "style_code")), targets=MAPPING_TARGETS
            )

    def test_without_targets_old_rules_unchanged(self) -> None:
        cfg = validate_mapping_config(_cols(("a", "category")))
        assert cfg["columns"][0]["target_field"] == "category"


class TestOldTargetNamesStillWork:
    def test_brand_code_and_season_parsed_for_goods_layer(self) -> None:
        """AC 40 附：旧目标名 brand_code / season 的自定义映射照常生效（写商品层由 adapter 决定）。"""

        class _M:
            mapping_config: ClassVar[dict[str, Any]] = {
                "columns": _cols(*_MINIMAL, ("牌子", "brand_code"), ("季", "season"))
            }

        parsed = StyleSkuImportAdapter().parse_row(
            {
                "款号": "S1",
                "商品编码": "K1",
                "商品名称": "裙",
                "颜色及规格": "红;M",
                "牌子": "LN",
                "季": "夏",
            },
            _M(),  # type: ignore[arg-type]
        )
        assert (parsed["brand_code"], parsed["season"]) == ("LN", "夏")
        assert (parsed["color"], parsed["size"]) == ("红", "M")
