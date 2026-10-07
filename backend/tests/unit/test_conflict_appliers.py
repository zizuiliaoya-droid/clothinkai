"""8a-6：applier 的校验与留痕（设计 §4.5.1、§4.5.2；本 FEAT 只有博主）。"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from app.modules.importer.conflict_appliers import (
    CONFLICT_APPLIERS,
    GENERIC_INVALID_REASON,
    ApplierValueError,
    BloggerApplier,
    build_object_audit,
    invalid_reason,
)

_APPLIER = BloggerApplier()


class _FakeSession:
    def __init__(self) -> None:
        self.flushes = 0

    async def flush(self) -> None:
        self.flushes += 1


def _blogger(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "nickname": "小美",
        "platform": "小红书",
        "wechat": None,
        "phone": None,
        "follower_count": 1000,
        "blogger_type": "腰部",
        "gender_target": None,
        "category_tags": ["美妆"],
        "quality_tags": [],
        "quote": Decimal("60.00"),
        "cooperation_history": None,
        "remark": None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.unit
class TestBloggerCheck:
    def test_registered(self) -> None:
        assert isinstance(CONFLICT_APPLIERS["blogger"], BloggerApplier)
        assert CONFLICT_APPLIERS["blogger"].audit_action == "blogger.update"
        assert CONFLICT_APPLIERS["blogger"].audit_resource == "blogger"

    @pytest.mark.parametrize(
        ("field", "value", "reason"),
        [
            ("nickname", "  ", "不能为空"),
            ("nickname", "x" * 129, "超过 128 字"),
            ("platform", "x" * 17, "超过 16 字"),
            ("blogger_type", "x" * 17, "超过 16 字"),
            ("gender_target", "x" * 17, "超过 16 字"),
            ("wechat", "x" * 65, "超过 64 字"),
            ("phone", "1" * 33, "超过 32 字"),
            ("follower_count", -1, "必须为 0 到 2147483647 之间的整数"),
            ("follower_count", 2**31, "必须为 0 到 2147483647 之间的整数"),
            ("follower_count", "12", GENERIC_INVALID_REASON),
            ("quote", "-0.01", "必须为非负数字且小于 1 亿"),
            ("quote", "100000000", "必须为非负数字且小于 1 亿"),
            ("category_tags", [f"t{i}" for i in range(21)], "最多 20 项"),
            ("quality_tags", "美妆", GENERIC_INVALID_REASON),
            ("nickname", None, "不能为空"),
        ],
    )
    def test_invalid(self, field: str, value: Any, reason: str) -> None:
        with pytest.raises(ApplierValueError) as exc_info:
            _APPLIER.check(field, value)
        assert exc_info.value.field == field
        assert exc_info.value.reason == reason
        # 原因只用固定文案，不含值
        if isinstance(value, str) and len(value) > 3:
            assert value not in exc_info.value.reason

    @pytest.mark.parametrize(
        ("field", "value", "expected"),
        [
            ("nickname", " 小美 ", "小美"),
            ("wechat", "x" * 64, "x" * 64),
            ("follower_count", 2**31 - 1, 2**31 - 1),
            ("follower_count", 0, 0),
            ("quote", "99999999.99", Decimal("99999999.99")),
            ("quote", "60", Decimal("60.00")),
            ("category_tags", ["美妆", " 护肤 "], ["美妆", "护肤"]),
            ("remark", "x" * 5000, "x" * 5000),
        ],
    )
    def test_valid(self, field: str, value: Any, expected: Any) -> None:
        assert _APPLIER.check(field, value) == expected

    def test_invalid_reason_never_uses_exception_text(self) -> None:
        class _M(BaseModel):
            cost: int

        with pytest.raises(ValidationError) as exc_info:
            _M(cost="60.00-secret")  # type: ignore[arg-type]
        assert invalid_reason(exc_info.value) == GENERIC_INVALID_REASON
        assert invalid_reason(ValueError("60.00")) == GENERIC_INVALID_REASON
        assert invalid_reason(InvalidOperation()) == GENERIC_INVALID_REASON
        assert invalid_reason(ApplierValueError("quote", "固定文案")) == "固定文案"


@pytest.mark.unit
class TestBloggerApply:
    async def test_follower_count_does_not_recompute_type(self) -> None:
        obj = _blogger(follower_count=1000, blogger_type="腰部")
        session = _FakeSession()
        changes = await _APPLIER.apply(session, obj, {"follower_count": 2_000_000})  # type: ignore[arg-type]
        assert changes == {"follower_count": (1000, 2_000_000)}
        assert obj.follower_count == 2_000_000
        assert obj.blogger_type == "腰部"
        assert session.flushes == 1

    async def test_same_value_not_written(self) -> None:
        obj = _blogger(quote=Decimal("60.00"), category_tags=["美妆", "护肤"])
        session = _FakeSession()
        changes = await _APPLIER.apply(
            session,  # type: ignore[arg-type]
            obj,
            {"quote": Decimal("60"), "category_tags": ["护肤", "美妆"]},
        )
        assert changes == {}
        assert session.flushes == 0

    def test_current_values_normalized(self) -> None:
        obj = _blogger(quote=Decimal("60"), nickname=" 小美 ", category_tags=[])
        values = _APPLIER.current_values(obj, ["quote", "nickname", "category_tags", "wechat"])
        assert values == {
            "quote": "60.00",
            "nickname": "小美",
            "category_tags": None,
            "wechat": None,
        }


@pytest.mark.unit
class TestObjectAudit:
    def test_sensitive_only_changed_flag(self) -> None:
        batch_id = uuid4()
        before, after = build_object_audit(
            _APPLIER,
            {
                "quote": (Decimal("60.00"), Decimal("65.00")),
                "wechat": (None, "wx"),
                "phone": ("1", "2"),
                "remark": (None, " 新备注 "),
                "follower_count": (1000, 2000),
                "category_tags": (["b"], ["b", "a"]),
            },
            via="import_fill",
            batch_id=batch_id,
            row_number=3,
        )
        assert before == {"remark": None, "follower_count": 1000, "category_tags": ["b"]}
        assert after["quote_changed"] is True
        assert after["wechat_changed"] is True
        assert after["phone_changed"] is True
        for name in ("quote", "wechat", "phone"):
            assert name not in after
            assert name not in before
        assert after["remark"] == " 新备注 "  # TEXT 不去空白
        assert after["category_tags"] == ["a", "b"]
        assert after["via"] == "import_fill"
        assert after["import_batch_id"] == str(batch_id)
        assert after["row_number"] == 3
        assert "import_conflict_id" not in after
        json.dumps(before)
        dumped = json.dumps(after, ensure_ascii=False)
        assert "65.00" not in dumped
        assert "wx" not in dumped

    def test_single_field_and_conflict_id(self) -> None:
        conflict_id = uuid4()
        before, after = build_object_audit(
            _APPLIER,
            {"quote": (Decimal("60.00"), Decimal("65.00"))},
            via="import_conflict",
            batch_id=None,
            conflict_id=conflict_id,
        )
        assert before == {}
        assert after == {
            "quote_changed": True,
            "via": "import_conflict",
            "import_batch_id": None,
            "import_conflict_id": str(conflict_id),
        }
        json.dumps(after)
