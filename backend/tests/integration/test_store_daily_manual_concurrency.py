"""店铺日手填 PUT 的并发首录（7e R4 评审 r1 L2）。

审计的 before 来自写前的 ``SELECT … FOR UPDATE``。这一天还没有行时 ``FOR UPDATE`` 锁不住任何东西：
两个请求同时首录都读到 ``before = None``，后到的经 ``ON CONFLICT DO UPDATE`` 覆盖了先到的值，
审计却记成首录。所以 ``upsert_manual`` 先按 (租户, 日期) 拿事务级 advisory lock，再读 before。

必须用**两条独立连接**：``session`` fixture 是单连接 + savepoint，同一事务里锁可重入，测不出并发。
这里直接用 ``engine`` 开连接、真实提交，结束时清掉自己建的租户。
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog
from app.modules.report.advanced_schemas import StoreDailyManualUpdate
from app.modules.report.store_daily_service import StoreDailyService, _manual_lock_key
from app.modules.report.work_progress_models import StoreDaily

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))")


async def _make_tenant(maker: async_sessionmaker[AsyncSession]) -> Any:
    tid = uuid4()
    async with maker() as s:
        await s.execute(
            text(
                "INSERT INTO tenant (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, '并发首录租户', 'active', NOW(), NOW())"
            ),
            {"id": tid, "code": f"conc_sd_{uuid4().hex[:8]}"},
        )
        await s.commit()
    return tid


async def _drop_tenant(maker: async_sessionmaker[AsyncSession], tid: Any) -> None:
    async with maker() as s:
        await s.execute(text("DELETE FROM audit_log WHERE tenant_id = :t"), {"t": tid})
        await s.execute(text("DELETE FROM store_daily WHERE tenant_id = :t"), {"t": tid})
        await s.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": tid})
        await s.commit()


async def test_concurrent_first_entry_audit_before_sees_committed_value(engine: Any) -> None:
    """一个首录还没提交时，另一个首录要等它提交，再把它的值记成自己的 before。

    holder 拿同一把锁、插入首录的行但不提交（模拟进行中的首录）；other 走 ``upsert_manual``。
    """
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    tid = await _make_tenant(maker)
    day = date(2026, 5, 20)
    tok = tenant_id_ctx.set(tid)
    try:
        async with maker() as holder:
            await holder.execute(_LOCK_SQL, {"key": _manual_lock_key(tid, day)})
            holder.add(StoreDaily(tenant_id=tid, date=day, zhitongche_spend=Decimal("40.00")))
            await holder.flush()

            async with maker() as other:
                task = asyncio.create_task(
                    StoreDailyService(other).upsert_manual(
                        tid,
                        day,
                        StoreDailyManualUpdate(zhitongche_spend=Decimal("80.00")),
                        cast(Any, SimpleNamespace(id=None)),
                    )
                )
                await asyncio.sleep(0.5)
                assert not task.done(), "第二个首录应等第一个提交"
                await holder.commit()
                row = await asyncio.wait_for(task, timeout=10)
                assert row.zhitongche_spend == Decimal("80.00")

        async with maker() as reader:
            entries = (
                (
                    await reader.execute(
                        select(AuditLog)
                        .where(
                            AuditLog.tenant_id == tid,
                            AuditLog.action == "report.store_daily.update",
                        )
                        .order_by(AuditLog.id)
                    )
                )
                .scalars()
                .all()
            )
        assert [(e.before, e.after) for e in entries] == [
            (
                {
                    "ad_spend_total": None,
                    "zhitongche_spend": "40.00",
                    "yinli_spend": None,
                    "refund_only_amount": None,
                    "remark": None,
                },
                {
                    "date": str(day),
                    "ad_spend_total": None,
                    "zhitongche_spend": "80.00",
                    "yinli_spend": None,
                    "refund_only_amount": None,
                    "remark": None,
                },
            )
        ]
    finally:
        tenant_id_ctx.reset(tok)
        await _drop_tenant(maker, tid)


async def test_lock_is_per_day(engine: Any) -> None:
    """锁按 (租户, 日期)：别的日子在录时，这一天照常写，不排队。"""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    tid = await _make_tenant(maker)
    tok = tenant_id_ctx.set(tid)
    try:
        async with maker() as holder:
            await holder.execute(_LOCK_SQL, {"key": _manual_lock_key(tid, date(2026, 5, 20))})
            async with maker() as other:
                row = await asyncio.wait_for(
                    StoreDailyService(other).upsert_manual(
                        tid,
                        date(2026, 5, 21),
                        StoreDailyManualUpdate(yinli_spend=Decimal("5.00")),
                        cast(Any, SimpleNamespace(id=None)),
                    ),
                    timeout=5,
                )
                assert row.yinli_spend == Decimal("5.00")
            await holder.rollback()
    finally:
        tenant_id_ctx.reset(tok)
        await _drop_tenant(maker, tid)
