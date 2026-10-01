"""催发任务的权限域隔离（纯规则，不碰数据库）。

这里守的是批次 3 踩过的那个坑：``EffectivePermissions.has`` 的前缀通配**只看 scope
的第一段**。所以 scope 名字起在哪个一级域下，直接决定谁能用。

催发故意分两个域：
- ``promotion.urge:*`` 要被 PR 的 ``promotion.*:*`` 命中（催发是 PR 日常工作）
- ``urge_config:*`` 必须独立，否则 PR 能把「催过 3 次提示主管」的阈值改成 999
"""

from __future__ import annotations

import pytest

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.urge.permissions import (
    SCOPE_URGE,
    SCOPE_URGE_CONFIG,
)

pytestmark = [pytest.mark.unit]


def _perms_for(role_code: str) -> EffectivePermissions:
    for role in DEFAULT_ROLES:
        if role.code == role_code:
            return EffectivePermissions(user_id="u", scopes=frozenset(role.permissions))
    raise AssertionError(f"预设角色 {role_code} 不存在")


class TestUrgeTaskScope:
    @pytest.mark.parametrize("role_code", ["pr", "pr_manager"])
    def test_pr_roles_get_urge_via_promotion_wildcard(self, role_code: str) -> None:
        """PR 与主管靠 promotion.*:* 通配拿到催发读写，migration 不必显式授权。"""
        perms = _perms_for(role_code)
        assert perms.has(SCOPE_URGE, "read") is True
        assert perms.has(SCOPE_URGE, "write") is True

    def test_operations_read_only(self) -> None:
        """运营持 promotion.*:read —— 能看催发进度，不能发起催发。"""
        perms = _perms_for("operations")
        assert perms.has(SCOPE_URGE, "read") is True
        assert perms.has(SCOPE_URGE, "write") is False

    def test_finance_read_only(self) -> None:
        """财务显式授了 promotion.urge:read（没有 promotion 通配）。"""
        perms = _perms_for("finance")
        assert perms.has(SCOPE_URGE, "read") is True
        assert perms.has(SCOPE_URGE, "write") is False

    def test_warehouse_cannot_see_urge(self) -> None:
        """仓库持 promotion:read + promotion.warehouse:write。

        两条都匹配不上 promotion.urge —— ``promotion:read`` 不是通配形式，
        ``promotion.warehouse:write`` 是另一个具体 scope。
        """
        perms = _perms_for("warehouse")
        assert perms.has(SCOPE_URGE, "read") is False
        assert perms.has(SCOPE_URGE, "write") is False

    def test_designer_cannot_see_urge(self) -> None:
        perms = _perms_for("designer")
        assert perms.has(SCOPE_URGE, "read") is False
        assert perms.has(SCOPE_URGE, "write") is False


class TestUrgeConfigScope:
    def test_pr_cannot_touch_config(self) -> None:
        """核心断言：PR 的 promotion.*:* 不能碰阈值。

        这正是 ``urge_config`` 不挂在 ``promotion.`` 下的理由 —— 挂过去的话
        下面两条都会变成 True，PR 就能自己把提示阈值调到永不触发。
        """
        perms = _perms_for("pr")
        assert perms.has(SCOPE_URGE_CONFIG, "read") is False
        assert perms.has(SCOPE_URGE_CONFIG, "write") is False

    def test_manager_can_touch_config(self) -> None:
        perms = _perms_for("pr_manager")
        assert perms.has(SCOPE_URGE_CONFIG, "read") is True
        assert perms.has(SCOPE_URGE_CONFIG, "write") is True

    def test_admin_wildcard_covers_config(self) -> None:
        perms = _perms_for("admin")
        assert perms.has(SCOPE_URGE_CONFIG, "write") is True

    @pytest.mark.parametrize("role_code", ["finance", "operations", "warehouse", "merchandiser"])
    def test_others_cannot_touch_config(self, role_code: str) -> None:
        perms = _perms_for(role_code)
        assert perms.has(SCOPE_URGE_CONFIG, "write") is False


class TestScopeNaming:
    def test_urge_config_is_not_under_promotion(self) -> None:
        """名字本身就是防线。有人把它改到 promotion. 下这条会红。"""
        assert not SCOPE_URGE_CONFIG.startswith("promotion")
        assert "." not in SCOPE_URGE_CONFIG

    def test_urge_task_is_under_promotion(self) -> None:
        """反过来，催发任务必须在 promotion 下才能被 PR 的通配覆盖。"""
        assert SCOPE_URGE.split(".", 1)[0] == "promotion"
