"""覆盖失效 helper（7e R2，报表视图设计 3.3）。

等锁那几条必须用**两条独立连接**：``session`` fixture 是单连接 + savepoint，同一事务里
advisory lock 可重入，测不出「等正在跑的刷新」。写法照 ``test_summary_refresh_concurrency``：
直接用 ``engine`` 开连接、真实提交，结束时清理自己建的租户。
"""

from __future__ import annotations

import asyncio
import time
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.report import summary_invalidation
from app.modules.report.exceptions import SummaryRefreshBusyError
from app.modules.report.summary_invalidation import (
    invalidate_summary_coverage,
    summary_lock_key,
)
from app.modules.report.summary_refresh_service import _lock_key

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

LO, HI = date(2026, 3, 1), date(2026, 3, 3)

_SEED_SQL = text(
    """
    INSERT INTO report_summary_coverage
      (id, tenant_id, stat_date, refreshed_at, created_at, updated_at)
    SELECT gen_random_uuid(), CAST(:t AS uuid), d::date, NOW(), NOW(), NOW()
    FROM generate_series(CAST(:lo AS date), CAST(:hi AS date), interval '1 day') AS d
    """
)
_COUNT_SQL = text("SELECT count(*) FROM report_summary_coverage WHERE tenant_id = :t")
_HOLD_SQL = text("SELECT pg_try_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))")


async def _seed(s: AsyncSession, tid: Any, hi: date = HI) -> None:
    await s.execute(_SEED_SQL, {"t": tid, "lo": LO, "hi": hi})


async def _count(s: AsyncSession, tid: Any) -> int:
    return int((await s.execute(_COUNT_SQL, {"t": tid})).scalar_one())


async def _lock_timeout(s: AsyncSession) -> str:
    return str((await s.execute(text("SHOW lock_timeout"))).scalar_one())


async def _make_tenant(maker: async_sessionmaker[AsyncSession]) -> Any:
    tid = uuid4()
    async with maker() as s:
        await s.execute(
            text(
                "INSERT INTO tenant (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, '失效测试租户', 'active', NOW(), NOW())"
            ),
            {"id": tid, "code": f"inv_sum_{uuid4().hex[:8]}"},
        )
        await _seed(s, tid)
        await s.commit()
    return tid


async def _drop_tenant(maker: async_sessionmaker[AsyncSession], tid: Any) -> None:
    async with maker() as s:
        await s.execute(
            text("DELETE FROM report_summary_coverage WHERE tenant_id = :t"), {"t": tid}
        )
        await s.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": tid})
        await s.commit()


async def test_deletes_only_own_tenant_and_restores_lock_timeout(
    session: AsyncSession, tenant_a: Any, tenant_b: Any
) -> None:
    """只删本租户、返回行数；拿到锁后 lock_timeout 已还原，不影响业务事务后续语句。"""
    # 刷新与失效必须锁同一个键
    assert _lock_key(tenant_a.id) == summary_lock_key(tenant_a.id)
    await _seed(session, tenant_a.id)
    await _seed(session, tenant_b.id, hi=date(2026, 3, 2))
    original = await _lock_timeout(session)

    rows = await invalidate_summary_coverage(
        session, tenant_id=tenant_a.id, reason="platform_link_change"
    )

    assert rows == 3
    assert await _count(session, tenant_a.id) == 0
    assert await _count(session, tenant_b.id) == 2
    assert await _lock_timeout(session) == original
    # 再清一次：没有覆盖就是 0 行，不报错
    assert await invalidate_summary_coverage(session, tenant_id=tenant_a.id, reason="again") == 0


async def test_waits_for_running_refresh_then_clears(engine: Any) -> None:
    """刷新在跑（另一连接持锁）：helper 阻塞等它提交，再清覆盖 —— 不是 try 一下就走。"""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    tid = await _make_tenant(maker)
    try:
        async with maker() as holder, maker() as other:
            got = (await holder.execute(_HOLD_SQL, {"key": summary_lock_key(tid)})).scalar_one()
            assert got is True
            original = await _lock_timeout(other)

            async def _release_later() -> None:
                await asyncio.sleep(0.5)
                await holder.rollback()

            releaser = asyncio.create_task(_release_later())
            started = time.monotonic()
            rows = await invalidate_summary_coverage(
                other, tenant_id=tid, reason="platform_link_change"
            )
            waited = time.monotonic() - started
            await releaser
            assert rows == 3
            assert waited >= 0.4, f"没等刷新锁就清了（{waited:.3f}s）"
            assert await _lock_timeout(other) == original
            await other.commit()
        async with maker() as check:
            assert await _count(check, tid) == 0
    finally:
        await _drop_tenant(maker, tid)


async def test_busy_when_refresh_holds_lock_past_timeout(
    engine: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """刷新一直不放锁：等到 lock_timeout 抛忙；回滚后覆盖还在、lock_timeout 是原值。"""
    monkeypatch.setattr(summary_invalidation, "LOCK_TIMEOUT", "300ms")
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    tid = await _make_tenant(maker)
    try:
        async with maker() as holder, maker() as other:
            got = (await holder.execute(_HOLD_SQL, {"key": summary_lock_key(tid)})).scalar_one()
            assert got is True
            original = await _lock_timeout(other)
            started = time.monotonic()
            with pytest.raises(SummaryRefreshBusyError) as exc_info:
                await invalidate_summary_coverage(
                    other, tenant_id=tid, reason="platform_link_change"
                )
            assert time.monotonic() - started >= 0.25
            assert exc_info.value.code == "REPORT_SUMMARY_REFRESH_BUSY"
            assert exc_info.value.status_code == 409
            assert exc_info.value.message == "报表正在刷新，请稍后再保存"
            await other.rollback()
            assert await _lock_timeout(other) == original
            assert await _count(other, tid) == 3
            await holder.rollback()
    finally:
        await _drop_tenant(maker, tid)
