"""流程线测试基建（设计 9.2）：``flow_users`` 七个真实角色账号与 ``run_concurrently``。

``flow_users`` 的角色取迁移 seed 的 role / role_permission，这里经
``AuthService.load_effective_permissions``（直接读库）断言各账号拿到的是真实授权：
以前 PR 与主管都用 admin_role，「两级不同人」和「不持 ``*``」测不出来。
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.service import AuthService
from tests.concurrency import committed, default_tenant_id, run_concurrently

# 只给本文件用的序号日期，避开 test_promotion_concurrency 的 2026-05-27
_DK = date(2026, 1, 3)


async def _perms(session: AsyncSession, user: Any) -> EffectivePermissions:
    return await AuthService(session).load_effective_permissions(user.id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestFlowUsers:
    async def test_admin_holds_star(self, session: AsyncSession, flow_users: Any) -> None:
        perms = await _perms(session, flow_users.admin)
        assert "*" in perms.scopes

    async def test_pr_and_pr2_are_real_pr(self, session: AsyncSession, flow_users: Any) -> None:
        assert flow_users.pr.id != flow_users.pr2.id
        for user in (flow_users.pr, flow_users.pr2):
            perms = await _perms(session, user)
            assert "promotion.*:*" in perms.scopes
            assert "*" not in perms.scopes
            assert not perms.has("negotiation.review", "approve")

    async def test_pr_manager_can_review_negotiation(
        self, session: AsyncSession, flow_users: Any
    ) -> None:
        perms = await _perms(session, flow_users.pr_manager)
        assert "*" not in perms.scopes
        assert perms.has("negotiation.review", "approve")

    async def test_finance(self, session: AsyncSession, flow_users: Any) -> None:
        perms = await _perms(session, flow_users.finance)
        assert perms.has("finance.settlement", "pay")
        assert "promotion.*:*" not in perms.scopes
        assert not perms.has("promotion", "write")

    async def test_operations_reads_promotion_only(
        self, session: AsyncSession, flow_users: Any
    ) -> None:
        perms = await _perms(session, flow_users.operations)
        assert "promotion.*:read" in perms.scopes
        assert "promotion.*:*" not in perms.scopes
        assert not perms.has("promotion", "write")

    async def test_warehouse_scopes_exact(self, session: AsyncSession, flow_users: Any) -> None:
        perms = await _perms(session, flow_users.warehouse)
        # 060 收回 promotion:read；promotion.warehouse:write 仅为回滚保留
        assert perms.scopes == frozenset(
            {"promotion_ship:fill", "promotion_ship:export", "promotion.warehouse:write"}
        )
        assert not perms.has("promotion", "read")

    async def test_seven_distinct_accounts_same_tenant(self, flow_users: Any) -> None:
        users = [
            flow_users.pr,
            flow_users.pr2,
            flow_users.pr_manager,
            flow_users.admin,
            flow_users.finance,
            flow_users.operations,
            flow_users.warehouse,
        ]
        assert len({u.id for u in users}) == 7
        assert {u.tenant_id for u in users} == {flow_users.tenant.id}


@pytest.mark.integration
@pytest.mark.asyncio
class TestRunConcurrently:
    async def test_each_attempt_gets_own_session_and_tenant_ctx(self, engine: Any) -> None:
        tid = await default_tenant_id(engine)
        before = tenant_id_ctx.get()

        async def attempt(s: AsyncSession, i: int) -> tuple[int, int, UUID | None, int]:
            # pg_backend_pid 证明每次 attempt 是独立连接（不是同一个 session 串着跑）
            pid = (await s.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            return i, id(s), tenant_id_ctx.get(), pid

        results = await run_concurrently(engine, 5, attempt)

        assert [r[0] for r in results] == [0, 1, 2, 3, 4]
        assert len({r[1] for r in results}) == 5, "attempt 之间共用了 session"
        assert len({r[3] for r in results}) == 5, "attempt 之间共用了连接"
        assert {r[2] for r in results} == {tid}
        assert tenant_id_ctx.get() == before

    async def test_explicit_tenant_and_exceptions_propagate(self, engine: Any) -> None:
        tid = await default_tenant_id(engine)

        async def boom(s: AsyncSession, i: int) -> None:
            assert tenant_id_ctx.get() == tid
            if i == 1:
                raise RuntimeError("attempt 1 炸了")

        with pytest.raises(RuntimeError, match="attempt 1 炸了"):
            await run_concurrently(engine, 3, boom, tenant_id=tid)

    async def test_committed_is_visible_to_other_connections(self, engine: Any) -> None:
        tid = await default_tenant_id(engine)
        async with committed(engine) as s:
            await s.execute(
                text("DELETE FROM promotion_sequence WHERE tenant_id = :tid AND date_key = :dk"),
                {"tid": tid, "dk": _DK},
            )
            await s.execute(
                text(
                    "INSERT INTO promotion_sequence "
                    "(id, tenant_id, date_key, last_seq, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :tid, :dk, 7, NOW(), NOW())"
                ),
                {"tid": tid, "dk": _DK},
            )
        try:

            async def read(s: AsyncSession, i: int) -> int:
                row = await s.execute(
                    text(
                        "SELECT last_seq FROM promotion_sequence "
                        "WHERE tenant_id = :tid AND date_key = :dk"
                    ),
                    {"tid": tid, "dk": _DK},
                )
                return int(row.scalar_one())

            assert await run_concurrently(engine, 2, read) == [7, 7]
        finally:
            async with committed(engine) as s:
                await s.execute(
                    text(
                        "DELETE FROM promotion_sequence WHERE tenant_id = :tid AND date_key = :dk"
                    ),
                    {"tid": tid, "dk": _DK},
                )
