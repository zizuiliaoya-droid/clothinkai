"""迁移 061_report_view_permissions 在 head 状态下的 seed（报表视图设计第 4 节）。

往返与按生产形态的演练（个人授权、既有授权保留、pr 补回）在测试库上手工跑，结果写报告，不进仓库。
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.report.advanced_permissions import REPORT_VIEW_PERMISSIONS

# scope → 库里持有它的系统角色（显式授权，不展开通配）
_EXPECTED: dict[str, set[str]] = {
    "report.work_progress:read_own": {"pr"},
    "report.publish_progress:read_own": {"pr"},
    "report.bi:read": {"finance"},
    "report.bi:read_own": {"pr"},
    "report.work_progress:read": {"finance"},
    # pr_manager / operations 由 021 授
    "report.export:read": {"pr_manager", "operations", "finance"},
    # 018 授
    "report.store_daily:write": {"operations"},
    # admin 由 052 授
    "report.summary:refresh": {"admin", "pr_manager", "operations"},
}

_REVOKED = "report.publish_progress:read"


async def _roles_for(session: AsyncSession, scope: str) -> set[str]:
    return set(
        (
            await session.execute(
                text(
                    "SELECT r.code FROM role_permission rp "
                    "JOIN role r ON r.id = rp.role_id "
                    "JOIN permission p ON p.id = rp.permission_id "
                    "WHERE p.scope = CAST(:s AS text) AND r.is_system"
                ),
                {"s": scope},
            )
        )
        .scalars()
        .all()
    )


def _code_roles_for(scope: str) -> set[str]:
    return {r.code for r in DEFAULT_ROLES if scope in r.permissions}


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigration061Seed:
    async def test_new_scopes_registered(self, session: AsyncSession) -> None:
        new = [f"{s}:{a}" for s, a, _ in REPORT_VIEW_PERMISSIONS]
        rows = (
            await session.execute(
                text("SELECT scope, category FROM permission WHERE scope = ANY(:s)"),
                {"s": new},
            )
        ).all()
        assert sorted(r.scope for r in rows) == sorted(new)
        assert {r.category for r in rows} == {"function"}

    @pytest.mark.parametrize("scope", sorted(_EXPECTED))
    async def test_role_set(self, session: AsyncSession, scope: str) -> None:
        in_db = await _roles_for(session, scope)
        assert in_db == _EXPECTED[scope]
        assert in_db == _code_roles_for(scope)

    async def test_pr_publish_progress_read_revoked(self, session: AsyncSession) -> None:
        assert "pr" not in await _roles_for(session, _REVOKED)
        assert "pr" not in _code_roles_for(_REVOKED)
        # scope 本身还在册：下行补回要用
        n = (
            await session.execute(
                text("SELECT count(*) FROM permission WHERE scope = CAST(:s AS text)"),
                {"s": _REVOKED},
            )
        ).scalar_one()
        assert n == 1

    async def test_operations_keeps_store_daily_write(self, session: AsyncSession) -> None:
        assert "operations" in await _roles_for(session, "report.store_daily:write")
