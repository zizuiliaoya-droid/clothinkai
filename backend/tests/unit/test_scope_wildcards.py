"""8b：博主标签字典维护权 ``blogger_tag:write`` 不能被现有通配捞到（设计 §5.1）。

``has()`` 的前缀通配只看第一段：叫 ``blogger.tag:write`` 就会被 PR 的 ``blogger.*:*`` 命中。
迁移 059 不 import app 代码，所以另用 ast 读它的常量，与 ``default_roles`` 对一遍。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import (
    BLOGGER_ALL,
    BLOGGER_READ,
    BLOGGER_TAG_WRITE,
    DEFAULT_ROLES,
    PRODUCT_ALL,
)
from app.modules.auth.permissions import SCOPE_ALL

_MIGRATION_059 = (
    Path(__file__).resolve().parents[2] / "alembic" / "versions" / "059_8b_blogger_library.py"
)


def _split(full: str) -> tuple[str, str]:
    scope, action = full.rsplit(":", 1)
    return scope, action


def _has(scopes: set[str] | tuple[str, ...], full: str) -> bool:
    scope, action = _split(full)
    return EffectivePermissions(user_id="u", scopes=frozenset(scopes)).has(scope, action)


def _role(code: str) -> tuple[str, ...]:
    return next(r for r in DEFAULT_ROLES if r.code == code).permissions


def _migration_constants() -> dict[str, object]:
    tree = ast.parse(_MIGRATION_059.read_text(encoding="utf-8"))
    out: dict[str, object] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return out


class TestBloggerTagScopeWildcards:
    def test_scope_is_its_own_top_level_domain(self) -> None:
        assert BLOGGER_TAG_WRITE == "blogger_tag:write"

    @pytest.mark.parametrize("wildcard", [BLOGGER_ALL, BLOGGER_READ, PRODUCT_ALL])
    def test_existing_wildcards_do_not_match(self, wildcard: str) -> None:
        assert _has({wildcard}, BLOGGER_TAG_WRITE) is False

    def test_scenario_wildcards_are_effective(self) -> None:
        """场景有效性：同样的通配确实能命中博主域的写权限（否则上一条是假绿）。"""
        assert _has({BLOGGER_ALL}, "blogger:write") is True
        assert _has({BLOGGER_ALL}, "blogger.tag:write") is True

    @pytest.mark.parametrize("code", ["pr", "operations", "merchandiser", "finance"])
    def test_roles_without_grant(self, code: str) -> None:
        assert _has(_role(code), BLOGGER_TAG_WRITE) is False

    @pytest.mark.parametrize("code", ["pr_manager", "admin", "platform_admin"])
    def test_roles_with_grant(self, code: str) -> None:
        assert _has(_role(code), BLOGGER_TAG_WRITE) is True

    def test_only_pr_manager_lists_it_explicitly(self) -> None:
        holders = {r.code for r in DEFAULT_ROLES if BLOGGER_TAG_WRITE in r.permissions}
        assert holders == {"pr_manager"}
        # 管理员靠 *，不显式列
        assert SCOPE_ALL in _role("admin")


class TestMigration059Constants:
    def test_scope_and_grant_match_default_roles(self) -> None:
        consts = _migration_constants()
        assert consts["_TAG_SCOPE"] == BLOGGER_TAG_WRITE
        holders = {r.code for r in DEFAULT_ROLES if BLOGGER_TAG_WRITE in r.permissions}
        assert consts["_TAG_GRANT_ROLES"] == tuple(sorted(holders))
