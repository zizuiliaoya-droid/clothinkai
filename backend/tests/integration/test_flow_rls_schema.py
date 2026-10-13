"""流程线 schema 护栏（设计 9.2）：新表 RLS 与「迁移授权 = DEFAULT_ROLES」一致性。

两份清单随各流程线 PR 追加（PR-1 先各放一个现有对象，保证不空跑）：

- ``RLS_TABLES``：每张表必须 ENABLE + FORCE RLS，且有作用于 ``clothing_app`` 的
  ``tenant_isolation`` policy。漏写 ``enable_rls_sql`` 的新表在这里红
  （test_rls.py 打了 ``rls`` 标记、CI 跳过；这里只读系统表，不打标记，CI 照跑）。
- ``ROLE_SCOPES``：每个 scope 在库里 ``role_permission`` 的系统角色集合，
  必须与 ``DEFAULT_ROLES`` 里显式列出它的角色集合精确相等。生产授权只来自迁移，
  单测用 ``DEFAULT_ROLES``，两边会漂。只比显式授权，不展开通配（admin 的 ``*`` 两边都不算）。

另有 ``line_event`` 只追加（PR-3）：``clothing_app`` 有 INSERT / SELECT、没有 UPDATE / DELETE，
``seq`` 的隐式序列有 USAGE——漏了序列权限，所有写事件的动作都会失败（事件与业务同事务）。
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.default_roles import DEFAULT_ROLES

# 每个流程线 PR 建的新表加到这里（PR-2 起：promotion_item、negotiation_item、line_event……）
RLS_TABLES: tuple[str, ...] = (
    "import_conflict",
    "promotion_item",
    # PR-3（063）
    "line_event",
    "negotiation_item",
    # PR-6（065）；PR-8 才用，空表也要有 RLS
    "return_freight_payment",
)

# 每个流程线 PR 新增 / 改授权的 scope 加到这里。
# 现有两条：一条只授给单个角色，一条授给多个角色。
ROLE_SCOPES: tuple[str, ...] = (
    "negotiation.review:approve",
    "negotiation:read",
    # PR-2（060）
    "promotion_ship:push",
    "promotion_ship:fill",
    "promotion_ship:export",
    # 7e R1（061）。收回的 report.publish_progress:read 不放这里，test_migration_061_state 单独断言
    "report.work_progress:read_own",
    "report.publish_progress:read_own",
    "report.bi:read",
    "report.bi:read_own",
    "report.work_progress:read",
    "report.export:read",
    "report.store_daily:write",
    "report.summary:refresh",
    # 7e R2（062）只加汇总列、不改授权，这里没有条目
    # PR-3（063）；negotiation_final:approve 只入册不绑角色，见 test_migration_063_state
    "negotiation_all:read",
    # PR-5（064）
    "promotion_create:write",
    # PR-6（065）；4 条字段 scope 只入册不绑角色，见 test_migration_065_state
    "promotion_review:approve",
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


@pytest.mark.integration
@pytest.mark.asyncio
class TestLineEventAppendOnlyPrivileges:
    async def _table_priv(self, session: AsyncSession, priv: str) -> bool:
        return bool(
            (
                await session.execute(
                    text(
                        "SELECT has_table_privilege(CAST(:r AS text), 'public.line_event', "
                        "CAST(:p AS text))"
                    ),
                    {"r": _APP_ROLE, "p": priv},
                )
            ).scalar_one()
        )

    async def test_insert_select_only(self, session: AsyncSession) -> None:
        got = {
            p: await self._table_priv(session, p) for p in ("INSERT", "SELECT", "UPDATE", "DELETE")
        }
        assert got == {"INSERT": True, "SELECT": True, "UPDATE": False, "DELETE": False}

    async def test_seq_sequence_usage(self, session: AsyncSession) -> None:
        seq = (
            await session.execute(text("SELECT pg_get_serial_sequence('public.line_event', 'seq')"))
        ).scalar_one()
        # 场景有效性：seq 确实是 identity，有隐式序列
        assert seq == "public.line_event_seq_seq"
        usage = (
            await session.execute(
                text("SELECT has_sequence_privilege(CAST(:r AS text), CAST(:s AS text), 'USAGE')"),
                {"r": _APP_ROLE, "s": seq},
            )
        ).scalar_one()
        assert usage is True
