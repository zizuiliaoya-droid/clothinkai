"""U03 Blogger 业务规则单元测试（domain.py）。

覆盖：
- BR-U03-30 audit 字段白名单 + 敏感值脱敏
- compute_blogger_changes：dict diff 正确性
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.modules.blogger.domain import (
    BLOGGER_SENSITIVE_FIELDS,
    BLOGGER_SENSITIVE_VALUE_FIELDS,
    build_blogger_audit_changes,
    compute_blogger_changes,
)
from app.modules.blogger.models import Blogger
from app.modules.blogger.schemas import BloggerUpdate


class TestBloggerAuditChanges:
    def test_sensitive_value_fields_redacted(self) -> None:
        changes = {
            "quote": {"before": "100.00", "after": "200.00"},
            "wechat": {"before": "old_wx", "after": "new_wx"},
            "phone": {"before": "13800000000", "after": "13900000000"},
            "xiaohongshu_id": {"before": "OLD", "after": "NEW"},
            "nickname": {"before": "旧昵称", "after": "新昵称"},
            "remark": {"before": "old", "after": "new"},
        }
        audit = build_blogger_audit_changes(changes)

        # 敏感值字段：仅记标记
        assert audit["quote_changed"] is True
        assert audit["wechat_changed"] is True
        assert audit["phone_changed"] is True
        assert "quote" not in audit
        assert "wechat" not in audit
        assert "phone" not in audit

        # 非敏感值（但在白名单内）：正常 before/after
        assert audit["xiaohongshu_id"] == {"before": "OLD", "after": "NEW"}
        assert audit["nickname"] == {"before": "旧昵称", "after": "新昵称"}

        # 不在白名单：不写
        assert "remark" not in audit

    def test_non_sensitive_excluded(self) -> None:
        changes = {
            "remark": {"before": "x", "after": "y"},
            "category_tags": {"before": [], "after": ["穿搭"]},
            "follower_count": {"before": 1000, "after": 2000},
        }
        audit = build_blogger_audit_changes(changes)
        assert audit == {}

    def test_constants_well_defined(self) -> None:
        assert BLOGGER_SENSITIVE_FIELDS >= BLOGGER_SENSITIVE_VALUE_FIELDS
        assert "quote" in BLOGGER_SENSITIVE_VALUE_FIELDS
        assert "wechat" in BLOGGER_SENSITIVE_VALUE_FIELDS
        assert "phone" in BLOGGER_SENSITIVE_VALUE_FIELDS
        assert "xiaohongshu_id" in BLOGGER_SENSITIVE_FIELDS
        assert "xiaohongshu_id" not in BLOGGER_SENSITIVE_VALUE_FIELDS
        assert "nickname" in BLOGGER_SENSITIVE_FIELDS
        assert "nickname" not in BLOGGER_SENSITIVE_VALUE_FIELDS


class TestComputeBloggerChanges:
    def _new_blogger(self, **kw: object) -> Blogger:
        defaults: dict[str, object] = {
            "tenant_id": uuid4(),
            "xiaohongshu_id": "XHS001",
            "nickname": "测试",
            "platform": "小红书",
            "category_tags": [],
            "quality_tags": [],
            "is_suspected_fake": False,
            "is_active": True,
            "is_deleted": False,
        }
        defaults.update(kw)
        return Blogger(**defaults)  # type: ignore[arg-type]

    def test_unchanged_returns_empty(self) -> None:
        blogger = self._new_blogger(nickname="保持")
        payload = BloggerUpdate(nickname="保持")
        assert compute_blogger_changes(blogger, payload) == {}

    def test_quote_change_detected(self) -> None:
        blogger = self._new_blogger(quote=Decimal("100.00"))
        payload = BloggerUpdate(quote=Decimal("200.00"))
        changes = compute_blogger_changes(blogger, payload)
        assert "quote" in changes
        assert changes["quote"]["before"] == "100.00"
        assert changes["quote"]["after"] == "200.00"

    def test_only_set_fields_in_diff(self) -> None:
        """未在 payload 显式设置的字段不出现在 changes（PATCH 语义）."""
        blogger = self._new_blogger(nickname="原")
        payload = BloggerUpdate(remark="新备注")  # 仅设 remark
        changes = compute_blogger_changes(blogger, payload)
        assert set(changes.keys()) == {"remark"}
