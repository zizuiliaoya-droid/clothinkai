"""平台指标配置的权限域隔离（纯规则，不碰数据库；报表视图设计 3.4、第 4 节）。

``metric_config`` 必须是独立一级域：``has()`` 的前缀通配只看第一段，挂在 ``promotion.`` 下会被
PR 的 ``promotion.*:*`` 命中（PR 能自己调爆文阈值），挂在 ``report.`` 下会被运营 / 主管的
``report.*:read`` 捞走读权限。
"""

from __future__ import annotations

import pytest

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.metric_config.permissions import (
    SCOPE_METRIC_CONFIG,
    SCOPE_METRIC_CONFIG_READ,
    SCOPE_METRIC_CONFIG_WRITE,
)

pytestmark = [pytest.mark.unit]


def _perms_for(role_code: str) -> EffectivePermissions:
    role = next(r for r in DEFAULT_ROLES if r.code == role_code)
    return EffectivePermissions(user_id=role_code, scopes=frozenset(role.permissions))


def _can(perms: EffectivePermissions) -> tuple[bool, bool]:
    return perms.has(SCOPE_METRIC_CONFIG, "read"), perms.has(SCOPE_METRIC_CONFIG, "write")


class TestRoleMatrix:
    @pytest.mark.parametrize(
        ("role_code", "expected"),
        [
            ("admin", (True, True)),
            ("platform_admin", (True, True)),
            ("pr_manager", (True, True)),
            ("operations", (True, False)),
            ("pr", (False, False)),
            ("finance", (False, False)),
            ("merchandiser", (False, False)),
            ("warehouse", (False, False)),
            ("designer", (False, False)),
        ],
    )
    def test_read_write(self, role_code: str, expected: tuple[bool, bool]) -> None:
        assert _can(_perms_for(role_code)) == expected

    def test_explicit_grants_in_default_roles(self) -> None:
        holders = {
            scope: {r.code for r in DEFAULT_ROLES if scope in r.permissions}
            for scope in (SCOPE_METRIC_CONFIG_READ, SCOPE_METRIC_CONFIG_WRITE)
        }
        assert holders == {
            SCOPE_METRIC_CONFIG_READ: {"pr_manager", "operations"},
            SCOPE_METRIC_CONFIG_WRITE: {"pr_manager"},
        }


class TestWildcardsDoNotHit:
    @pytest.mark.parametrize(
        "wildcard",
        ["promotion.*:*", "promotion.*:read", "report.*:read", "blogger.*:*", "product.*:*"],
    )
    def test_existing_wildcards(self, wildcard: str) -> None:
        perms = EffectivePermissions(user_id="u", scopes=frozenset({wildcard}))
        assert _can(perms) == (False, False)

    def test_scope_naming(self) -> None:
        """名字本身就是防线：改到 promotion. / report. 下这条会红。"""
        assert SCOPE_METRIC_CONFIG == "metric_config"
        assert "." not in SCOPE_METRIC_CONFIG
        assert SCOPE_METRIC_CONFIG_READ == f"{SCOPE_METRIC_CONFIG}:read"
        assert SCOPE_METRIC_CONFIG_WRITE == f"{SCOPE_METRIC_CONFIG}:write"
