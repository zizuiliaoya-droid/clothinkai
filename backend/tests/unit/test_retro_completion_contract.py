"""M5 的权限边界与完结持久化契约。"""

import pytest
from sqlalchemy import CheckConstraint

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.promotion.models import Promotion

pytestmark = pytest.mark.unit


def test_only_supervisor_gets_independent_retro_scope_by_default() -> None:
    assert {r.code for r in DEFAULT_ROLES if "promotion_retro:write" in r.permissions} == {
        "pr_manager"
    }
    for role in DEFAULT_ROLES:
        perms = EffectivePermissions(user_id="contract", scopes=frozenset(role.permissions))
        assert perms.has("promotion_retro", "write") == (
            role.code in {"pr_manager", "admin", "platform_admin"}
        ), role.code


def test_completion_is_nullable_timestamp_and_three_retro_states() -> None:
    completed = Promotion.__table__.columns.get("completed_at")
    assert completed is not None
    assert completed.nullable and completed.type.timezone
    check = next(
        c
        for c in Promotion.__table__.constraints
        if isinstance(c, CheckConstraint) and "retro_status" in str(c.sqltext)
    )
    assert "待确认" not in str(check.sqltext)
