"""U02 Style 业务规则单元测试。

覆盖：
- BR-U02-30 仅 style_code 写 audit
- compute_style_changes：dict diff 正确性
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.product.domain import (
    STYLE_SENSITIVE_FIELDS,
    build_style_audit_changes,
    compute_style_changes,
)
from app.modules.product.models import Style
from app.modules.product.schemas import StyleUpdate


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
