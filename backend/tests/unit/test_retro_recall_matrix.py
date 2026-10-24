"""M1：已发布召回中保留并行复盘能力；未发布已取消召回不开放。"""

from dataclasses import replace

import pytest

from app.modules.flow.matrix import ui_for
from tests.unit.fixtures.flow_matrix_expected import DOCS
from tests.unit.test_flow_matrix import PERSONAS

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "role,status,action",
    [
        ("pr_manager", "待复盘", "retro_write"),
        ("admin", "已完成", "retro_revise"),
    ],
)
def test_published_recalling_keeps_retro_entry_and_detail(role, status, action) -> None:
    doc = replace(DOCS["promotion"]["召回中·已结款"], retro_status=status)
    assert doc.publish_status == "已发布" and doc.completed_at is None
    assert action in doc.allowed_actions()
    ui = ui_for(PERSONAS[role], doc)
    assert action in ui.actions
    assert ui.actions[action].state == "enabled"
    # 使用真实响应序列化边界验证字段投影，不只验证内部cell级别。
    detail = ui.to_dict(view="detail")
    assert detail["fields"]["retro"]["state"] == "edit"
    assert ui.to_dict(view="list")["actions"][action]["state"] == "enabled"


def test_unpublished_cancelled_recalling_has_no_retro_capabilities() -> None:
    doc = replace(DOCS["promotion"]["召回中"], retro_status="未开始")
    assert doc.publish_status == "已取消"
    for role in ("pr_manager", "admin"):
        ui = ui_for(PERSONAS[role], doc)
        assert "retro_write" not in ui.actions and "retro_revise" not in ui.actions
        assert "retro" not in ui.fields


@pytest.mark.parametrize("publish_status", ["未发布", "已取消"])
@pytest.mark.parametrize("retro_status", ["待复盘", "已完成"])
def test_unpublished_recall_cannot_gain_capabilities_from_retro_state(
    publish_status, retro_status
) -> None:
    doc = replace(
        DOCS["promotion"]["召回中"], publish_status=publish_status, retro_status=retro_status
    )
    for role in ("pr_manager", "admin"):
        ui = ui_for(PERSONAS[role], doc)
        assert "retro" not in ui.fields
        assert "retro_write" not in ui.actions and "retro_revise" not in ui.actions


def test_recalling_write_still_requires_independent_scope_and_revision_true_star() -> None:
    from app.core.security.permissions import EffectivePermissions

    doc = replace(DOCS["promotion"]["召回中·已结款"], retro_status="待复盘")
    manager = PERSONAS["pr_manager"]
    revoked = replace(
        manager,
        perms=EffectivePermissions(
            user_id=str(manager.user_id),
            scopes=manager.perms.scopes - {"promotion_retro:write"},
        ),
    )
    for who in (PERSONAS["pr"], revoked):
        ui = ui_for(who, doc)
        assert ui.fields["retro"].state == "read"
        assert ui.actions["retro_write"].state == "disabled"
    completed = replace(doc, retro_status="已完成")
    admin_without_star = replace(
        PERSONAS["admin"],
        perms=EffectivePermissions(
            user_id="synthetic-admin",
            scopes=frozenset({"promotion:read", "promotion:write", "promotion_retro:write"}),
        ),
    )
    for who in (manager, admin_without_star):
        ui = ui_for(who, completed)
        assert "retro_revise" not in ui.actions
        assert ui.fields["retro"].state == "read"
