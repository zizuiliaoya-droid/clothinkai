"""流程线 schema 护栏（设计 9.2）：新表 RLS 与「迁移授权 = DEFAULT_ROLES」一致性。

两份清单随各流程线 PR 追加（PR-1 先各放一个现有对象，保证不空跑）：

- ``RLS_TABLES``：每张表必须 ENABLE + FORCE RLS，且有作用于 ``clothing_app`` 的
  ``tenant_isolation`` policy。漏写 ``enable_rls_sql`` 的新表在这里红
  （test_rls.py 打了 ``rls`` 标记、CI 跳过；这里只读系统表，不打标记，CI 照跑）。
- ``ROLE_SCOPES``：每个 scope 在库里 ``role_permission`` 的系统角色集合，
  必须与 ``DEFAULT_ROLES`` 里显式列出它的角色集合精确相等。生产授权只来自迁移，
  单测用 ``DEFAULT_ROLES``，两边会漂。只比显式授权，不展开通配（admin 的 ``*`` 两边都不算）。

``line_event`` 的序列 USAGE 与 INSERT / SELECT-only 权限断言随 PR-3 加。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import DEFAULT_ROLES

# 每个流程线 PR 建的新表加到这里（PR-2 起：promotion_item、negotiation_item、line_event……）
RLS_TABLES: tuple[str, ...] = ("import_conflict",)

# 每个流程线 PR 新增 / 改授权的 scope 加到这里。
# 现有两条：一条只授给单个角色，一条授给多个角色。
ROLE_SCOPES: tuple[str, ...] = (
    "negotiation.review:approve",
    "negotiation:read",
)

_APP_ROLE = "clothing_app"
_POLICY = "tenant_isolation"


@dataclass(frozen=True)
class _RlsState:
    exists: bool
    enabled: bool
    forced: bool
    has_policy: bool

    @property
    def ok(self) -> bool:
        return self.exists and self.enabled and self.forced and self.has_policy


async def _rls_state(session: AsyncSession, table: str) -> _RlsState:
    row = (
        await session.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "WHERE c.oid = to_regclass(CAST(:t AS text))"
            ),
            {"t": f"public.{table}"},
        )
    ).one_or_none()
    if row is None:
        return _RlsState(exists=False, enabled=False, forced=False, has_policy=False)
    n_policy = (
        await session.execute(
            text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = CAST(:t AS text) "
                "AND policyname = CAST(:p AS text) AND CAST(:r AS name) = ANY(roles)"
            ),
            {"t": table, "p": _POLICY, "r": _APP_ROLE},
        )
    ).scalar_one()
    return _RlsState(
        exists=True,
        enabled=bool(row.relrowsecurity),
        forced=bool(row.relforcerowsecurity),
        has_policy=n_policy == 1,
    )


async def _db_roles_for(session: AsyncSession, scope: str) -> set[str]:
    return set(
        (
            await session.execute(
                text(
                    "SELECT DISTINCT r.code FROM role_permission rp "
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


def _default_roles_for(scope: str) -> set[str]:
    return {r.code for r in DEFAULT_ROLES if scope in r.permissions}


def test_lists_not_empty() -> None:
    """清单为空时下面的参数化用例一条都不跑，等于没测。"""
    assert RLS_TABLES
    assert ROLE_SCOPES
    assert len(set(RLS_TABLES)) == len(RLS_TABLES)
    assert len(set(ROLE_SCOPES)) == len(ROLE_SCOPES)


@pytest.mark.integration
@pytest.mark.asyncio
class TestFlowTablesRls:
    @pytest.mark.parametrize("table", RLS_TABLES)
    async def test_enable_force_and_policy(self, session: AsyncSession, table: str) -> None:
        state = await _rls_state(session, table)
        assert state == _RlsState(exists=True, enabled=True, forced=True, has_policy=True)

    async def test_helper_flags_table_without_rls(self, session: AsyncSession) -> None:
        """反向自检：没开 RLS 的表三项都得判成假，不存在的表也不通过。

        逐项比，免得某一项的查询失效被别的项盖住。
        """
        plain = await _rls_state(session, "alembic_version")
        assert plain == _RlsState(exists=True, enabled=False, forced=False, has_policy=False)
        assert plain.ok is False
        missing = await _rls_state(session, "no_such_table_flow_rls")
        assert missing.exists is False
        assert missing.ok is False


@pytest.mark.integration
@pytest.mark.asyncio
class TestMigrationGrantsMatchDefaultRoles:
    @pytest.mark.parametrize("scope", ROLE_SCOPES)
    async def test_role_set_equal(self, session: AsyncSession, scope: str) -> None:
        in_db = await _db_roles_for(session, scope)
        in_code = _default_roles_for(scope)
        # 场景有效性：两边都得真有授权，否则「空 == 空」假通过
        assert in_code, f"DEFAULT_ROLES 里没有角色显式持有 {scope}"
        assert in_db, f"库里 role_permission 没有角色持有 {scope}"
        assert in_db == in_code
