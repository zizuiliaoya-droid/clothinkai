"""U04 promotion domain.py 单元测试。

覆盖：
- BR-U04-40 audit 字段白名单 + 敏感值脱敏（quote_amount / cost_snapshot 仅记 `*_changed: true`）
- compute_promotion_changes：dict diff 正确性（source_extra 按合并结果比）
- merge_source_extra：source_extra 的 PATCH 按键合并（7a-5）
- format_internal_code：业务键格式化（BR-U04-01）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.modules.promotion.domain import (
    PROMOTION_SENSITIVE_FIELDS,
    PROMOTION_SENSITIVE_VALUE_FIELDS,
    build_promotion_audit_changes,
    compute_promotion_changes,
    compute_state_change,
    format_internal_code,
    merge_source_extra,
)
from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import PromotionUpdate


class TestPromotionAuditChanges:
    def test_sensitive_value_fields_redacted(self) -> None:
        changes = {
            "quote_amount": {"before": "100.00", "after": "200.00"},
            "cost_snapshot": {"before": "50.00", "after": "60.00"},
            "internal_code": {"before": "DE2605260001", "after": "DE2605260002"},
            "publish_url": {"before": None, "after": "https://x.com/note/1"},
            "publish_status": {"before": "未发布", "after": "已发布"},
            "remark": {"before": "old", "after": "new"},
        }
        audit = build_promotion_audit_changes(changes)

        # 敏感值：仅记 *_changed
        assert audit["quote_amount_changed"] is True
        assert audit["cost_snapshot_changed"] is True
        assert "quote_amount" not in audit
        assert "cost_snapshot" not in audit

        # 非敏感但白名单内：完整保留
        assert audit["internal_code"] == {
            "before": "DE2605260001",
            "after": "DE2605260002",
        }
        assert audit["publish_url"]["after"] == "https://x.com/note/1"
        assert audit["publish_status"]["after"] == "已发布"

        # 不在白名单：不写
        assert "remark" not in audit

    def test_constants(self) -> None:
        assert PROMOTION_SENSITIVE_FIELDS >= PROMOTION_SENSITIVE_VALUE_FIELDS
        assert "quote_amount" in PROMOTION_SENSITIVE_VALUE_FIELDS
        assert "cost_snapshot" in PROMOTION_SENSITIVE_VALUE_FIELDS
        assert "internal_code" in PROMOTION_SENSITIVE_FIELDS
        assert "internal_code" not in PROMOTION_SENSITIVE_VALUE_FIELDS

    def test_compute_state_change(self) -> None:
        result = compute_state_change(field="publish_status", before="未发布", after="已发布")
        assert result == {"publish_status": {"before": "未发布", "after": "已发布"}}

    def test_compute_state_change_unchanged(self) -> None:
        assert compute_state_change(field="publish_status", before="已发布", after="已发布") == {}


class TestComputePromotionChanges:
    def _new_promotion(self, **kw: object) -> Promotion:
        defaults: dict[str, object] = {
            "tenant_id": uuid4(),
            "style_id": uuid4(),
            "blogger_id": uuid4(),
            "internal_code": "DE2605260001",
            "style_code_snapshot": "ST001",
            "style_short_name_snapshot": "测试款",
            "quote_amount": Decimal("500.00"),
            "platform": "小红书",
            "cooperation_date": date(2026, 5, 26),
            "publish_status": "未发布",
            "recall_status": "未召回",
            "settlement_status": "未核查",
            "is_active": True,
        }
        defaults.update(kw)
        return Promotion(**defaults)  # type: ignore[arg-type]

    def test_unchanged_returns_empty(self) -> None:
        p = self._new_promotion(remark="保持")
        payload = PromotionUpdate(remark="保持")
        assert compute_promotion_changes(p, payload) == {}

    def test_quote_amount_change_detected(self) -> None:
        p = self._new_promotion(quote_amount=Decimal("100.00"))
        payload = PromotionUpdate(quote_amount=Decimal("200.00"))
        changes = compute_promotion_changes(p, payload)
        assert "quote_amount" in changes
        assert changes["quote_amount"]["before"] == "100.00"
        assert changes["quote_amount"]["after"] == "200.00"

    def test_only_set_fields_in_diff(self) -> None:
        p = self._new_promotion(remark="原")
        payload = PromotionUpdate(note_title="新标题")
        changes = compute_promotion_changes(p, payload)
        assert set(changes.keys()) == {"note_title"}

    def test_source_extra_patch_equal_to_current_is_no_change(self) -> None:
        """补丁合并后与现值相同 = 没改：值相同（去空白后）、删一个本来就没有的键。"""
        p = self._new_promotion(source_extra={"订单号": "TB001", "博主风格": "甜美"})
        assert compute_promotion_changes(p, PromotionUpdate(source_extra={"订单号": "TB001"})) == {}
        assert (
            compute_promotion_changes(p, PromotionUpdate(source_extra={"订单号": "  TB001 "})) == {}
        )
        assert compute_promotion_changes(p, PromotionUpdate(source_extra={"负责PR": None})) == {}
        assert compute_promotion_changes(p, PromotionUpdate(source_extra={})) == {}

    def test_source_extra_after_is_merged_result(self) -> None:
        p = self._new_promotion(source_extra={"订单号": "TB001", "博主风格": "甜美"})
        changes = compute_promotion_changes(
            p, PromotionUpdate(source_extra={"订单号": "TB002", "负责PR": "小王"})
        )
        assert changes["source_extra"]["before"] == {"订单号": "TB001", "博主风格": "甜美"}
        # after 是合并结果：没出现在补丁里的「博主风格」还在
        assert changes["source_extra"]["after"] == {
            "订单号": "TB002",
            "博主风格": "甜美",
            "负责PR": "小王",
        }
        # 只是算 diff，不能顺手改了 ORM 实例
        assert p.source_extra == {"订单号": "TB001", "博主风格": "甜美"}


class TestMergeSourceExtra:
    """source_extra 的 PATCH 按键合并（7a-5）。"""

    def test_keys_not_in_patch_are_kept(self) -> None:
        current = {"寄回单号": "SF0001", "点赞数": 120, "博主风格": "甜美", "订单号": "旧单号"}
        merged = merge_source_extra(current, {"订单号": "TB001"})
        # 旧字段（寄回单号 / 点赞数，含非字符串的历史值）与导入写的键原样保留
        assert merged == {
            "寄回单号": "SF0001",
            "点赞数": 120,
            "博主风格": "甜美",
            "订单号": "TB001",
        }

    def test_none_and_blank_delete_only_that_key(self) -> None:
        current = {"订单号": "TB001", "打单地址": "杭州", "负责PR": "小王"}
        assert merge_source_extra(current, {"订单号": None}) == {
            "打单地址": "杭州",
            "负责PR": "小王",
        }
        assert merge_source_extra(current, {"打单地址": "   "}) == {
            "订单号": "TB001",
            "负责PR": "小王",
        }
        assert merge_source_extra(current, {"负责PR": ""}) == {
            "订单号": "TB001",
            "打单地址": "杭州",
        }

    def test_deleting_absent_key_is_noop(self) -> None:
        assert merge_source_extra({"订单号": "TB001"}, {"负责PR": None}) == {"订单号": "TB001"}

    def test_values_are_stripped(self) -> None:
        assert merge_source_extra({}, {"订单号": "  TB001 \n"}) == {"订单号": "TB001"}

    def test_current_none_treated_as_empty(self) -> None:
        assert merge_source_extra(None, {"订单号": "TB001", "负责PR": None}) == {"订单号": "TB001"}

    def test_inputs_not_mutated(self) -> None:
        current = {"订单号": "旧", "博主风格": "甜美"}
        patch = {"订单号": " 新 ", "博主风格": None}
        merged = merge_source_extra(current, patch)
        assert merged == {"订单号": "新"}
        assert current == {"订单号": "旧", "博主风格": "甜美"}
        assert patch == {"订单号": " 新 ", "博主风格": None}
        assert merged is not current


class TestFormatInternalCode:
    def test_basic(self) -> None:
        result = format_internal_code(
            tenant_code="default",
            cooperation_date=date(2026, 5, 26),
            sequence=1,
        )
        assert result == "DE2605260001"

    def test_short_tenant_code_padded(self) -> None:
        result = format_internal_code(
            tenant_code="A", cooperation_date=date(2026, 1, 1), sequence=42
        )
        assert result.startswith("AX")  # 不足 2 字符补 X
        assert result == "AX2601010042"

    def test_empty_tenant_code(self) -> None:
        result = format_internal_code(tenant_code="", cooperation_date=date(2026, 1, 1), sequence=1)
        assert result == "XX2601010001"

    def test_sequence_padding(self) -> None:
        assert (
            format_internal_code(
                tenant_code="DEMO",
                cooperation_date=date(2026, 5, 26),
                sequence=9999,
            )
            == "DE2605269999"
        )

    def test_uppercase(self) -> None:
        result = format_internal_code(
            tenant_code="abc",
            cooperation_date=date(2026, 5, 26),
            sequence=1,
        )
        assert result == "AB2605260001"
