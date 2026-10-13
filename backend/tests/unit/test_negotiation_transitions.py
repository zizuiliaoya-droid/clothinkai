"""谈款 7 态 × 动作的状态机（流程线设计 3.2 转移表；9.3 B 块「转移全表」的纯规则部分）。

期望手写、不从 ``TRANSITIONS`` 推导：允许的格由该动作的执行人 ``require`` 通过；其余格不管谁来（本人、主管、管理员）
都是 422 ``ILLEGAL_STATE_TRANSITION``——状态机先于规则与卡点（3.1 校验顺序），``ui`` 里也不出现这个键（谈款没有灰格动作）。
接口级的转移全表（含 N1 新建、N12 转交）随 S6 ~ S9 在集成测试里补。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import AppException
from app.core.security.field_permissions import FieldPermissionContext
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.flow.matrix import ACTION_KEYS, FlowActor, require, ui_for
from app.modules.negotiation.enums import NegotiationStatus
from app.modules.negotiation.flow_doc import TRANSITIONS, NegotiationDoc, build_negotiation_doc
from app.modules.negotiation.models import Negotiation

PR_ID = UUID(int=1)
MANAGER_ID = UUID(int=3)
_SPECS = {r.code: r for r in DEFAULT_ROLES}


def _actor(code: str, user_id: UUID) -> FlowActor:
    scopes = frozenset(_SPECS[code].permissions)
    return FlowActor(
        user_id=user_id,
        perms=EffectivePermissions(user_id=str(user_id), scopes=scopes),
        field_ctx=FieldPermissionContext(
            role_codes=frozenset({code}),
            grants=frozenset(),
            revokes=frozenset(),
            is_superuser="*" in scopes,
        ),
    )


PR = _actor("pr", PR_ID)
MANAGER = _actor("pr_manager", MANAGER_ID)
ADMIN = _actor("admin", uuid4())

STATES = (
    "草稿",
    "主管待审",
    "审核驳回",
    "待老板审核",
    "老板退回",
    "待补收货地址",
    "已定稿",
)

# 3.2 转移表（N2 ~ N11）按「从」抄；留言 N11 任意状态（PR-4），转交 N12 不进矩阵
ALLOWED: dict[str, set[str]] = {
    "草稿": {"edit", "submit", "comment"},
    "主管待审": {"manager_review", "comment"},
    "审核驳回": {"copy", "comment"},
    "待老板审核": {"final_review", "comment"},
    "老板退回": {"edit", "submit", "comment"},
    "待补收货地址": {"receiver_save", "receiver_confirm", "comment"},
    "已定稿": {"comment"},
}

# 每个动作的执行人（默认角色下）
EXECUTOR: dict[str, FlowActor] = {
    "edit": PR,
    "submit": PR,
    "copy": PR,
    "manager_review": MANAGER,
    "final_review": ADMIN,
    "receiver_save": PR,
    "receiver_confirm": PR,
    "comment": PR,
}

ACTIONS = ACTION_KEYS["negotiation"]


def _doc(state: str) -> NegotiationDoc:
    return NegotiationDoc(
        stage=state,
        state=state,
        column=state,
        owner_id=PR_ID,
        negotiator_id=PR_ID,
        reviewed_by=MANAGER_ID if state not in ("草稿", "主管待审") else None,
        blogger_id=UUID(int=11),
        style_id=UUID(int=12),
        items_complete=True,
        cooperation_mode="寄拍",
        platform="小红书",
        scheduled_publish_date=date(2026, 10, 20),
        quote_amount=Decimal("500.00"),
        receiver_name="张三",
        receiver_phone="13812345678",
        receiver_address="杭州市某路 1 号",
    )


def test_states_are_the_seven_stored_values() -> None:
    assert tuple(s.value for s in NegotiationStatus) == STATES
    assert set(TRANSITIONS) == set(STATES)
    assert set(ALLOWED) == set(STATES)


def test_every_action_reachable() -> None:
    """每个动作至少在一个状态允许；留言（comment，N11）每个状态都允许（PR-4）。"""
    reachable = set().union(*ALLOWED.values())
    assert reachable == set(EXECUTOR) == set(ACTIONS)
    for state in STATES:
        assert "comment" in TRANSITIONS[state], state


@pytest.mark.parametrize("state", STATES)
@pytest.mark.parametrize("action", ACTIONS)
def test_transition_cell(state: str, action: str) -> None:
    doc = _doc(state)
    if action in ALLOWED[state]:
        assert action in doc.allowed_actions()
        require(EXECUTOR[action], doc, action)
        assert ui_for(EXECUTOR[action], doc).actions[action].state == "enabled"
        return
    assert action not in doc.allowed_actions()
    for who in (PR, MANAGER, ADMIN):
        with pytest.raises(AppException) as exc:
            require(who, doc, action)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc.value.status_code == 422
        assert exc.value.details == {"from_state": state, "action": action}
        assert action not in ui_for(who, doc).actions


def test_state_machine_before_rules_and_gates() -> None:
    """状态机先判：别人的、缺项的已定稿单「提交」照样先报 422，而不是 403 / 缺项。"""
    doc = replace(
        _doc("已定稿"), owner_id=uuid4(), negotiator_id=uuid4(), scheduled_publish_date=None
    )
    with pytest.raises(AppException) as exc:
        require(PR, doc, "submit")
    assert exc.value.code == "ILLEGAL_STATE_TRANSITION"


def test_rejected_is_terminal_except_copy() -> None:
    """审核驳回（Q1）：不能再编辑、提交（旧行为是改完回草稿，B1），只能复制为新谈款。"""
    doc = _doc("审核驳回")
    assert doc.allowed_actions() == frozenset({"copy", "comment"})
    for action in ("edit", "submit"):
        with pytest.raises(AppException) as exc:
            require(PR, doc, action)
        assert exc.value.code == "ILLEGAL_STATE_TRANSITION"


def test_build_negotiation_doc_from_orm_and_row() -> None:
    reviewer = uuid4()
    neg = Negotiation(
        tenant_id=uuid4(),
        blogger_id=uuid4(),
        style_id=uuid4(),
        pr_id=PR_ID,
        cooperation_mode="置换",
        platform="抖音",
        scheduled_publish_date=date(2026, 11, 1),
        quote_amount=Decimal("0"),
        status="待老板审核",
        reviewed_by=reviewer,
        receiver_name=None,
        receiver_phone=None,
        receiver_address=None,
    )
    doc = build_negotiation_doc(neg, items_complete=False)
    assert (doc.stage, doc.state, doc.column) == ("待老板审核",) * 3
    assert doc.owner_id == doc.negotiator_id == PR_ID
    assert doc.reviewed_by == reviewer
    assert doc.items_complete is False
    assert doc.quote_amount == Decimal("0")
    assert doc.allowed_actions() == frozenset({"final_review", "comment"})

    row = {
        c: getattr(neg, c)
        for c in (
            "status",
            "pr_id",
            "reviewed_by",
            "blogger_id",
            "style_id",
            "cooperation_mode",
            "platform",
            "scheduled_publish_date",
            "quote_amount",
            "receiver_name",
            "receiver_phone",
            "receiver_address",
        )
    }
    assert build_negotiation_doc(row, items_complete=False) == doc
