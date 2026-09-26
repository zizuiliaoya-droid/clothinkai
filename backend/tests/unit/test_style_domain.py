"""U02 Style 业务规则单元测试。

覆盖：
- BR-U02-30 仅 style_code 写 audit
- compute_style_changes：dict diff 正确性
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.product.domain import (
    STYLE_SENSITIVE_FIELDS,
    SUITE_NAME_SEPARATOR,
    build_style_audit_changes,
    compute_style_changes,
)
from app.modules.product.models import Style
from app.modules.product.schemas import StyleCreate, StyleUpdate


class TestStyleAuditChanges:
    def test_only_style_code_in_audit(self) -> None:
        changes = {
            "style_code": {"before": "OLD", "after": "NEW"},
            "style_name": {"before": "旧名", "after": "新名"},
            "remark": {"before": "", "after": "新备注"},
            "tags": {"before": [], "after": ["夏季"]},
            "brand_id": {"before": None, "after": str(uuid4())},
        }
        audit = build_style_audit_changes(changes)
        assert "style_code" in audit
        assert "style_name" not in audit
        assert "remark" not in audit
        assert "tags" not in audit
        assert "brand_id" not in audit

    def test_no_audit_when_only_normal_fields(self) -> None:
        changes = {
            "style_name": {"before": "old", "after": "new"},
            "remark": {"before": None, "after": "remark"},
        }
        audit = build_style_audit_changes(changes)
        assert audit == {}

    def test_constants(self) -> None:
        assert STYLE_SENSITIVE_FIELDS == {"style_code"}


class TestComputeStyleChanges:
    def _new_style(self, **kw: object) -> Style:
        defaults: dict[str, object] = {
            "tenant_id": uuid4(),
            "style_code": "ST001",
            "style_name": "测试款式",
            "category": "连衣裙",
            "tags": [],
            "tag_color": [],
            "design_status": "大货",
            "is_active": True,
            "is_deleted": False,
        }
        defaults.update(kw)
        return Style(**defaults)  # type: ignore[arg-type]

    def test_unchanged_returns_empty(self) -> None:
        style = self._new_style()
        payload = StyleUpdate(style_name="测试款式")
        assert compute_style_changes(style, payload) == {}

    def test_style_name_change_detected(self) -> None:
        style = self._new_style()
        payload = StyleUpdate(style_name="新款式")
        changes = compute_style_changes(style, payload)
        assert changes["style_name"]["before"] == "测试款式"
        assert changes["style_name"]["after"] == "新款式"

    def test_only_set_fields_in_diff(self) -> None:
        style = self._new_style(remark="原备注")
        payload = StyleUpdate(remark="新备注")
        changes = compute_style_changes(style, payload)
        assert set(changes.keys()) == {"remark"}


class TestTagColorNormalization:
    """颜色明细（tag_color）录入校验。

    该字段此前全链路可用但前端无录入入口，所有款式都是空数组；开放录入后
    Create / Update 两条路径都必须做同样的清洗，否则编辑能写进空串与超长值。
    """

    def test_create_strips_and_dedupes(self) -> None:
        payload = StyleCreate(
            style_code="ST001",
            style_name="测试款式",
            category="连衣裙",
            tag_color=[" 卡其 ", "黑色", "卡其"],
        )
        assert payload.tag_color == ["卡其", "黑色"]

    def test_update_strips_and_dedupes(self) -> None:
        payload = StyleUpdate(tag_color=["黑色", " 黑色", "米白"])
        assert payload.tag_color == ["黑色", "米白"]

    def test_update_allows_explicit_clear(self) -> None:
        payload = StyleUpdate(tag_color=[])
        assert payload.tag_color == []
        assert "tag_color" in payload.model_fields_set

    def test_update_untouched_stays_none(self) -> None:
        payload = StyleUpdate(style_name="改名")
        assert payload.tag_color is None
        assert "tag_color" not in payload.model_fields_set

    @pytest.mark.parametrize("bad", [[""], ["   "], ["x" * 33]])
    def test_rejects_blank_and_overlong(self, bad: list[str]) -> None:
        with pytest.raises(ValidationError):
            StyleUpdate(tag_color=bad)
        with pytest.raises(ValidationError):
            StyleCreate(
                style_code="ST002",
                style_name="测试款式",
                category="连衣裙",
                tag_color=bad,
            )


class TestSuiteNameSeparator:
    """套装标题的连接符约定。

    标题本身由 migration 037 / 041 生成并存进 goods_main.goods_title
    （``string_agg(..., '+' ORDER BY style_code)``），不再在 Python 里按千牛ID 现拼。
    这个常量是两侧共用的约定，改它要同步改那两个迁移。
    """

    def test_separator_constant(self) -> None:
        assert SUITE_NAME_SEPARATOR == "+"
