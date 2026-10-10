"""7e R1 报表视图 scope 的通配边界与 4 个业务角色的判定表（纯规则，不连库）。

``has()`` 的前缀通配只看第一段、action 精确匹配：主管 / 运营持 ``report.*:read``，
要能命中新的 ``report.bi:read``（不用显式授），又不能命中 ``read_own``（否则它们会被当成
「只看本人」）。判定表按 ``DEFAULT_ROLES`` 构造，与迁移 061 的授权是同一份来源
（库里那一侧由 ``test_flow_rls_schema`` / ``test_migration_061_state`` 比）。
"""

from __future__ import annotations

import pytest

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.report.advanced_permissions import (
    REPORT_BI_READ,
    REPORT_BI_READ_OWN,
    REPORT_PUBLISH_PROGRESS_READ_OWN,
    REPORT_VIEW_PERMISSIONS,
    REPORT_WORK_PROGRESS_READ_OWN,
)

pytestmark = pytest.mark.unit


def _perms(*scopes: str) -> EffectivePermissions:
    return EffectivePermissions(user_id="u", scopes=frozenset(scopes))


def _role(code: str) -> EffectivePermissions:
    role = next(r for r in DEFAULT_ROLES if r.code == code)
    return _perms(*role.permissions)


class TestScopeShape:
    def test_four_new_scopes(self) -> None:
        assert [(s, a) for s, a, _ in REPORT_VIEW_PERMISSIONS] == [
            REPORT_WORK_PROGRESS_READ_OWN,
            REPORT_PUBLISH_PROGRESS_READ_OWN,
            REPORT_BI_READ,
            REPORT_BI_READ_OWN,
        ]

    def test_report_read_wildcard_hits_bi_read(self) -> None:
        assert _perms("report.*:read").has(*REPORT_BI_READ)

    @pytest.mark.parametrize(
        "pair",
        [REPORT_WORK_PROGRESS_READ_OWN, REPORT_PUBLISH_PROGRESS_READ_OWN, REPORT_BI_READ_OWN],
    )
    def test_report_read_wildcard_misses_read_own(self, pair: tuple[str, str]) -> None:
        assert not _perms("report.*:read").has(*pair)

    def test_read_own_does_not_imply_read(self) -> None:
        own = _perms(*(f"{s}:{a}" for s, a, _ in REPORT_VIEW_PERMISSIONS if a == "read_own"))
        for scope in ("report.work_progress", "report.publish_progress", "report.bi"):
            assert not own.has(scope, "read"), scope


# (scope, action) → 4 个角色的期望；列顺序 pr / pr_manager / finance / operations
_CHECKS: list[tuple[tuple[str, str], tuple[bool, bool, bool, bool]]] = [
    (("report.work_progress", "read"), (False, True, True, True)),
    (REPORT_WORK_PROGRESS_READ_OWN, (True, False, False, False)),
    (("report.publish_progress", "read"), (False, True, False, True)),
    (REPORT_PUBLISH_PROGRESS_READ_OWN, (True, False, False, False)),
    (REPORT_BI_READ, (False, True, True, True)),
    (REPORT_BI_READ_OWN, (True, False, False, False)),
    (("report.production", "read"), (False, True, False, True)),
    (("report.store_daily", "read"), (False, True, False, True)),
    (("report.store_daily", "write"), (False, False, False, True)),
    (("report.export", "read"), (False, True, True, True)),
    (("report.summary", "refresh"), (False, True, False, True)),
]
_ROLES = ("pr", "pr_manager", "finance", "operations")


class TestRoleTable:
    @pytest.mark.parametrize(
        ("pair", "expected"), _CHECKS, ids=[f"{s}:{a}" for (s, a), _ in _CHECKS]
    )
    def test_has(self, pair: tuple[str, str], expected: tuple[bool, ...]) -> None:
        got = tuple(_role(code).has(*pair) for code in _ROLES)
        assert got == expected

    def test_table_is_not_trivial(self) -> None:
        """场景有效性：每个角色都至少有一项真、一项假。"""
        for i, code in enumerate(_ROLES):
            column = {expected[i] for _, expected in _CHECKS}
            assert column == {True, False}, code

    def test_pr_no_longer_holds_publish_progress_read(self) -> None:
        pr = next(r for r in DEFAULT_ROLES if r.code == "pr")
        assert "report.publish_progress:read" not in pr.permissions
        assert not any("report.publish_progress:read" in r.permissions for r in DEFAULT_ROLES)
