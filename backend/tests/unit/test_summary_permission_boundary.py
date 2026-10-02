"""``report.summary:refresh`` 的权限边界（纯规则，不连库）。

这个端点会**删掉区间内的汇总行再重建**，权限给宽了等于把「触发全库重算」开放出去。

真正的风险在通配：``has()`` 的前缀匹配只看 scope 的第一段，``operations`` 与
``pr_manager`` 都持有 ``report.*:read``。如果刷新的 action 也叫 ``read``，这两个角色
就会自动拿到它 —— 这正是谈款审核当初必须独立成 ``negotiation`` 一级域的原因。

所以刷新的 action 刻意取 ``refresh``：``report.*:read`` 匹配不到它。这里把这件事
钉死，防止以后有人「为了统一」把它改成 ``read`` 或 ``write``。
"""

from __future__ import annotations

from app.core.security.permissions import EffectivePermissions
from app.modules.report.advanced_permissions import (
    REPORT_ADVANCED_PERMISSIONS,
    REPORT_SUMMARY_PERMISSIONS,
)

_SCOPE = "report.summary"
_ACTION = "refresh"


def has(scopes: set[str], scope: str, action: str) -> bool:
    return EffectivePermissions(user_id="t", scopes=frozenset(scopes)).has(scope, action)


class TestScopeShape:
    def test_scope_and_action_are_as_declared(self) -> None:
        assert REPORT_SUMMARY_PERMISSIONS == [
            (_SCOPE, _ACTION, "手动刷新报表汇总表"),
        ]

    def test_action_is_not_read_or_write(self) -> None:
        """action 不能是 read/write。

        ``report.*:read`` 被 operations / pr_manager 持有，``report.*:write``
        以后也可能被授出去。刷新用一个独立 action，通配匹配不到。
        """
        assert _ACTION not in {"read", "write"}

    def test_not_folded_into_an_existing_report_scope(self) -> None:
        """不能复用已有的报表 scope。

        挂在 report.production / report.store_daily 下，持有那张报表读写权的人
        就能触发跨 5 张表的重算，权限边界说不清。
        """
        existing = {s for s, _, _ in REPORT_ADVANCED_PERMISSIONS}
        assert _SCOPE not in existing


class TestWildcardDoesNotLeak:
    def test_report_read_wildcard_cannot_refresh(self) -> None:
        """持 ``report.*:read``（operations / pr_manager 的实际权限）不能刷新。"""
        assert not has({"report.*:read"}, _SCOPE, _ACTION)

    def test_report_write_wildcard_cannot_refresh(self) -> None:
        assert not has({"report.*:write"}, _SCOPE, _ACTION)

    def test_single_report_permissions_cannot_refresh(self) -> None:
        for scope, action, _ in REPORT_ADVANCED_PERMISSIONS:
            assert not has(
                {f"{scope}:{action}"}, _SCOPE, _ACTION
            ), f"{scope}:{action} 不该隐含刷新权"

    def test_global_admin_can_refresh(self) -> None:
        """admin / platform_admin 持有的全局 ``*`` 要能用。"""
        assert has({"*"}, _SCOPE, _ACTION)

    def test_explicit_grant_works(self) -> None:
        assert has({f"{_SCOPE}:{_ACTION}"}, _SCOPE, _ACTION)

    def test_report_wildcard_with_matching_action_does_grant(self) -> None:
        """反向确认通配是按 action 精确匹配的，不是「只看第一段就放行」。

        ``report.*:refresh`` 命中、``report.*:read`` 不命中 —— 这个差别正是
        上面那组测试成立的前提。真要有人授出 ``report.*:refresh`` 或
        ``report.*:*``，那是显式决定，不是意外泄漏。
        """
        assert has({f"report.*:{_ACTION}"}, _SCOPE, _ACTION)
        assert has({"report.*:*"}, _SCOPE, _ACTION)
