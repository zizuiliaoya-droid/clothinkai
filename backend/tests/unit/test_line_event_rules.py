"""时间线读接口的规则底座（流程线设计 6.5 第 2、3 层；PR-4 S2）。

- 第 2 层：``line_event.<分组>`` 字段规则的默认角色 = 6.5 的表
- 第 3 层：``PAYLOAD_FIELD_RULES`` 白名单 + ``filter_payload``（没登记的键删、不可读删、``snapshot`` / ``diff`` 内层按键再查）、
  ``ATTACHMENT_FIELD_RULES``（收款码类附件）
- ``next_step``：22 个阶段 + 谈款 7 态都有一格
落库读出（``GET /api/lines/{line_id}/events``）在 ``tests/integration/test_line_timeline.py``。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.core.security.field_permissions import (
    FIELD_PERMISSION_REGISTRY,
    FieldPermissionContext,
)
from app.modules.flow.line_events import (
    ATTACHMENT_FIELD_RULES,
    CONTAINER,
    EVENT_GROUP,
    EVENT_GROUP_KEYS,
    PAYLOAD_FIELD_RULES,
    LineEventType,
    attachment_readable,
    filter_payload,
)
from app.modules.flow.next_step import (
    NEGOTIATION_NEXT_STEP,
    PROMOTION_NEXT_STEP,
    NextStep,
    next_step,
)
from app.modules.negotiation.enums import NegotiationStatus
from app.modules.negotiation.service import _snapshot_diff, _submit_snapshot
from app.modules.promotion.stage_calculator import PROMOTION_STAGES
from tests.flow_event_helpers import assert_payload_registered

RECEIVER_KEYS = ("receiver_name", "receiver_phone", "receiver_address")


def _ctx(
    *roles: str,
    grants: set[str] | None = None,
    revokes: set[str] | None = None,
    superuser: bool = False,
) -> FieldPermissionContext:
    return FieldPermissionContext(
        role_codes=frozenset(roles),
        grants=frozenset(grants or set()),
        revokes=frozenset(revokes or set()),
        is_superuser=superuser,
    )


def _review_payload() -> dict[str, Any]:
    """推广审核通过 + 一份带金额与收件的 diff：各层都有「组」「Q」「R」三类键。"""
    return {
        "payable": "300.00",
        "destination": "待付款",
        "diff": {
            "quote_amount": {"before": "200.00", "after": "300.00"},
            "receiver_name": {"before": "甲", "after": "乙"},
            "platform": {"before": "小红书", "after": "抖音"},
        },
        "snapshot": {"quote_amount": "300.00", "platform": "抖音", "blogger_id": "b1"},
    }


# --- 第 2 层：分组规则 ---------------------------------------------------------

_GROUP_ROLES = {
    "negotiation": {"admin", "pr", "pr_manager", "finance", "operations"},
    "comment": {"admin", "pr", "pr_manager", "finance", "operations"},
    "promotion": {"admin", "pr", "pr_manager", "operations"},
    "shipping": {"admin", "pr", "pr_manager", "operations", "warehouse"},
    "settlement": {"admin", "pr", "pr_manager", "finance", "operations"},
    "retro": {"admin", "pr", "pr_manager", "operations"},
}


def test_line_event_group_rules_follow_design_table() -> None:
    """6.5 第 2 层的表：6 个分组各一条字段规则，默认可见角色逐个比，不可写。"""
    rules = FIELD_PERMISSION_REGISTRY["line_event"]
    assert set(rules) == set(EVENT_GROUP_KEYS) == set(_GROUP_ROLES)
    for key, roles in _GROUP_ROLES.items():
        assert rules[key].visible_roles == frozenset(roles), key
        assert rules[key].writable_roles == frozenset(), key


def test_finance_sees_only_three_groups() -> None:
    """11-23：财务只看谈款、留言、结款三组（推广、发货、复盘都看不到）。"""
    from app.core.security.field_permissions import can_read_field

    ctx = _ctx("finance")
    seen = {k for k in EVENT_GROUP_KEYS if can_read_field("line_event", k, ctx)}
    assert seen == {"negotiation", "comment", "settlement"}


def test_new_retro_events_join_retro_group() -> None:
    """D12：旧复盘的确认 / 打回补写进复盘组。"""
    assert EVENT_GROUP[LineEventType.RETRO_CONFIRMED] == "retro"
    assert EVENT_GROUP[LineEventType.RETRO_RETURNED] == "retro"


# --- 第 3 层：payload 白名单 ---------------------------------------------------


def test_rules_point_to_registered_fields() -> None:
    """规则指向的 (entity, field) 必须在注册表：没登记的字段 ``can_read_field`` 一律放行，等于没过滤。"""
    targets = [r for r in PAYLOAD_FIELD_RULES.values() if isinstance(r, tuple)]
    targets += list(ATTACHMENT_FIELD_RULES.values())
    assert targets, "场景有效性：至少有金额 / 收件 / 收款码三类规则"
    for entity, field in targets:
        assert field in FIELD_PERMISSION_REGISTRY.get(entity, {}), (entity, field)
    assert PAYLOAD_FIELD_RULES["quote_amount"] == ("promotion", "quote_amount")
    assert PAYLOAD_FIELD_RULES["payable"] == ("promotion", "quote_amount")
    for key in RECEIVER_KEYS:
        assert PAYLOAD_FIELD_RULES[key] == ("promotion", key)
    assert PAYLOAD_FIELD_RULES["snapshot"] is CONTAINER
    assert PAYLOAD_FIELD_RULES["diff"] is CONTAINER


def test_attachment_rules_keyed_by_existing_event_types() -> None:
    values = {t.value for t in LineEventType}
    assert set(ATTACHMENT_FIELD_RULES) <= values
    # 收款码、付款凭证、结款聊天截图（PR-6）同一条规则：运营、仓库看不到
    assert ATTACHMENT_FIELD_RULES == {
        LineEventType.PROMOTION_PAYMENT_QR_UPLOADED.value: ("promotion", "payment_qr"),
        LineEventType.SETTLEMENT_PAID.value: ("promotion", "payment_qr"),
        LineEventType.PROMOTION_SETTLED.value: ("promotion", "payment_qr"),
        LineEventType.RECALL_WAYBILL_UPLOADED.value: ("promotion", "payment_qr"),
        LineEventType.RECALL_SUCCEEDED.value: ("promotion", "payment_qr"),
        **{event.value: ("promotion", "payment_qr") for event in (
            LineEventType.FREIGHT_SUBMITTED, LineEventType.FREIGHT_APPROVED,
            LineEventType.FREIGHT_REJECTED, LineEventType.FREIGHT_PAID, LineEventType.FREIGHT_RESUBMITTED
        )},
    }


def test_payment_qr_rule_includes_finance_not_operations() -> None:
    """4.6：收款码可见 admin / pr / pr_manager / finance，运营、仓库看不到。"""
    rule = FIELD_PERMISSION_REGISTRY["promotion"]["payment_qr"]
    assert rule.visible_roles == frozenset({"admin", "pr", "pr_manager", "finance"})
    assert rule.writable_roles == frozenset({"admin", "pr", "pr_manager"})


def test_unregistered_key_is_dropped_even_for_superuser() -> None:
    """白名单：以后加 payload 键忘了登记，读接口直接删，不会漏出去。"""
    payload = {
        "destination": "待付款",
        "secret": "x",
        "diff": {"secret": {"before": 1, "after": 2}},
    }
    for ctx in (_ctx("pr"), _ctx("admin", superuser=True)):
        got = filter_payload(payload, ctx)
        assert got == {"destination": "待付款", "diff": {}}
    assert payload["secret"] == "x", "不改入参"


def test_operations_loses_amount_and_receiver_at_every_level() -> None:
    got = filter_payload(_review_payload(), _ctx("operations"))
    assert got == {
        "destination": "待付款",
        "diff": {"platform": {"before": "小红书", "after": "抖音"}},
        "snapshot": {"platform": "抖音", "blogger_id": "b1"},
    }


def test_finance_keeps_amount_loses_receiver() -> None:
    got = filter_payload(_review_payload(), _ctx("finance"))
    assert got["payable"] == "300.00"
    assert set(got["diff"]) == {"quote_amount", "platform"}
    assert got["snapshot"]["quote_amount"] == "300.00"


def test_pr_sees_everything_registered() -> None:
    payload = _review_payload()
    assert filter_payload(payload, _ctx("pr")) == payload


def test_personal_revoke_applies_to_payload() -> None:
    """个人撤销照常生效（4.6）：撤掉 PR 的服务费读权限 → 应付与内层服务费都删。"""
    ctx = _ctx("pr", revokes={"field.promotion.quote_amount:read"})
    got = filter_payload(_review_payload(), ctx)
    assert "payable" not in got
    assert "quote_amount" not in got["diff"] and "quote_amount" not in got["snapshot"]
    assert "receiver_name" in got["diff"]


def test_container_with_wrong_shape_is_dropped() -> None:
    """容器键的值不是 dict（形状不对）就整键删，不原样放出去。"""
    got = filter_payload({"diff": ["receiver_name"], "destination": "待付款"}, _ctx("operations"))
    assert got == {"destination": "待付款"}


@pytest.mark.parametrize(
    ("roles", "superuser", "expected"),
    [
        (("pr",), False, True),
        (("finance",), False, True),
        (("operations",), False, False),
        (("warehouse",), False, False),
        (("admin",), True, True),
    ],
)
def test_payment_qr_attachment_readable(
    roles: tuple[str, ...], superuser: bool, expected: bool
) -> None:
    ctx = _ctx(*roles, superuser=superuser)
    assert attachment_readable(LineEventType.PROMOTION_PAYMENT_QR_UPLOADED.value, ctx) is expected
    # 其余类型的附件（品牌词、7 天数据、留言贴图）只跟分组走，这一层不挡
    assert attachment_readable(LineEventType.LINE_COMMENT.value, ctx) is True
    assert attachment_readable(LineEventType.PROMOTION_METRICS_RECORDED, ctx) is True


def test_pr3_payload_shapes_are_registered() -> None:
    """PR-3 已经在写的 payload（提交快照与 diff、收件 diff、定稿、复制、转交、建单）键都已登记。"""
    negotiation = SimpleNamespace(
        blogger_id=uuid4(),
        style_id=uuid4(),
        goods_main_id=uuid4(),
        cooperation_mode="置换",
        platform="小红书",
        scheduled_publish_date=date(2026, 10, 1),
        quote_amount=Decimal("100"),
    )
    view = SimpleNamespace(style_id=uuid4(), sku_id=uuid4())
    snapshot = _submit_snapshot(negotiation, [view])  # type: ignore[arg-type]
    previous = {**snapshot, "platform": "抖音", "quote_amount": "90.00"}
    samples: list[dict[str, Any]] = [
        {"snapshot": snapshot, "diff": _snapshot_diff(previous, snapshot)},
        {"diff": {k: {"before": None, "after": "x"} for k in RECEIVER_KEYS}},
        {"promotion_id": "p"},
        {"from_negotiation_id": "n", "from_line_id": "l"},
        {"to_negotiation_id": "n", "to_line_id": "l"},
        {"from_pr_id": "a", "from_pr_name": "甲", "to_pr_id": "b", "to_pr_name": "乙"},
        {"source": "谈款定稿", "negotiation_id": "n"},
    ]
    for payload in samples:
        assert_payload_registered(payload)
        assert filter_payload(payload, _ctx("pr")) == payload


def test_assert_payload_registered_catches_inner_key() -> None:
    with pytest.raises(AssertionError):
        assert_payload_registered({"diff": {"secret": {"before": 1, "after": 2}}})
    with pytest.raises(AssertionError):
        assert_payload_registered({"secret": 1})


# --- next_step ----------------------------------------------------------------


def test_next_step_covers_every_stage_and_status() -> None:
    assert list(PROMOTION_NEXT_STEP) == list(PROMOTION_STAGES)
    assert list(NEGOTIATION_NEXT_STEP) == [s.value for s in NegotiationStatus]
    # 场景有效性：进行中的格都有「等谁做什么」，终态为 None
    for table in (PROMOTION_NEXT_STEP, NEGOTIATION_NEXT_STEP):
        for state, step in table.items():
            if step is not None:
                assert step.text and step.who, state
    terminal = {s for s, v in PROMOTION_NEXT_STEP.items() if v is None}
    assert terminal == {"已完结", "已完结 · 召回", "已完结 · 不合作", "已停用", "已删除"}
    assert {s for s, v in NEGOTIATION_NEXT_STEP.items() if v is None} == {"审核驳回", "已定稿"}


def test_next_step_prefers_promotion_stage() -> None:
    assert next_step(promotion_stage="待仓库发货", negotiation_status="已定稿") == NextStep(
        text="等待仓库打单发货", who="仓库"
    )
    assert (
        next_step(promotion_stage=None, negotiation_status="主管待审")
        == (NEGOTIATION_NEXT_STEP["主管待审"])
    )
    assert next_step(promotion_stage="已完结", negotiation_status="已定稿") is None
    assert next_step(promotion_stage=None, negotiation_status=None) is None
    # 没登记的值（库里出现了新状态）不报错，只是不提示
    assert next_step(promotion_stage="不存在", negotiation_status=None) is None
