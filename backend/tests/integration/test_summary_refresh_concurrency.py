"""汇总刷新的并发与覆盖记录。

5b-1 初版的刷新服务注释写着「两个请求撞上时最终写出同一份数据，不加锁」—— 那是
错的。「区间删 + 批量插」在 READ COMMITTED 下不能并发：后一个事务的 DELETE 看不到
前一个刚插入、尚未提交的行，它的 INSERT 会卡在唯一索引上，等前一个提交后报
``UniqueViolation``。触发场景很平常：每小时的定时任务撞上页面手动刷新。

这组测试必须用**两条独立连接**：``session`` fixture 是单连接 + savepoint，同一事务里
advisory lock 可重入，在那上面测不出并发问题。所以这里直接用 ``engine`` 开连接、
真实提交，结束时清理自己建的租户。

覆盖记录那几条也放在这里：它与锁同属「读取能不能信任汇总表」这一件事。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.report.exceptions import SummaryRefreshBusyError
from app.modules.report.summary_refresh_service import (
    SummaryRefreshService,
    _lock_key,
    shop_daily_span,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

LO, HI = date(2026, 3, 1), date(2026, 3, 31)

# 本文件建的租户要真实提交（跨连接可见），所以结束时要按依赖顺序清掉
_CLEANUP_TABLES = (
    "report_summary_coverage",
    "product_roi_summary",
    "pr_work_progress_summary",
    "shop_daily_summary",
    "shop_week_summary",
    "shop_month_summary",
    "qianniu_daily",
)


async def _make_tenant(maker: async_sessionmaker[AsyncSession]) -> Any:
    tid = uuid4()
    async with maker() as s:
        await s.execute(
            text(
                "INSERT INTO tenant (id, code, name, status, created_at, updated_at) "
                "VALUES (:id, :code, '并发测试租户', 'active', NOW(), NOW())"
            ),
            {"id": tid, "code": f"conc_sum_{uuid4().hex[:8]}"},
        )
        await s.commit()
    return tid


async def _drop_tenant(maker: async_sessionmaker[AsyncSession], tid: Any) -> None:
    async with maker() as s:
        for table in _CLEANUP_TABLES:
            await s.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tid})
        await s.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": tid})
        await s.commit()


class TestRefreshLock:
    async def test_second_concurrent_refresh_is_rejected_not_crashed(self, engine: Any) -> None:
        """一个刷新在进行中时，另一个同租户刷新要报「忙」，而不是撞唯一索引。

        用一条连接在事务里占住锁（模拟进行中的刷新），另一条连接去刷新。
        """
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        tid = await _make_tenant(maker)
        try:
            async with maker() as holder:
                # 占锁并保持事务打开 —— 事务级锁在这条事务结束前一直有效
                got = (
                    await holder.execute(
                        text(
                            "SELECT pg_try_advisory_xact_lock("
                            "hashtextextended(CAST(:key AS text), 0))"
                        ),
                        {"key": _lock_key(tid)},
                    )
                ).scalar_one()
                assert got is True

                async with maker() as other:
                    with pytest.raises(SummaryRefreshBusyError):
                        await SummaryRefreshService(other).refresh(
                            tenant_id=tid, date_from=LO, date_to=HI
                        )
                    await other.rollback()

                await holder.rollback()

            # 锁随事务结束释放，之后的刷新正常
            async with maker() as after:
                counts = await SummaryRefreshService(after).refresh(
                    tenant_id=tid, date_from=LO, date_to=HI
                )
                await after.commit()
            assert set(counts) == {
                "product_roi_summary",
                "pr_work_progress_summary",
                "shop_daily_summary",
                "shop_week_summary",
                "shop_month_summary",
            }
        finally:
            await _drop_tenant(maker, tid)

    async def test_lock_is_per_tenant(self, engine: Any) -> None:
        """A 租户刷新中，B 租户照常刷新 —— 锁不能是全局的。"""
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        tid_a = await _make_tenant(maker)
        tid_b = await _make_tenant(maker)
        try:
            async with maker() as holder:
                await holder.execute(
                    text(
                        "SELECT pg_try_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"
                    ),
                    {"key": _lock_key(tid_a)},
                )
                async with maker() as other:
                    # 不该抛
                    await SummaryRefreshService(other).refresh(
                        tenant_id=tid_b, date_from=LO, date_to=HI
                    )
                    await other.commit()
                await holder.rollback()
        finally:
            await _drop_tenant(maker, tid_a)
            await _drop_tenant(maker, tid_b)

    async def test_without_lock_overlapping_refreshes_would_collide(self, engine: Any) -> None:
        """证明锁是必要的：绕过锁、两个事务交错执行「区间删 + 插」会撞唯一索引。

        这条测的不是我们的代码，而是**前提**。如果有一天 PostgreSQL 的行为或隔离级别
        变了、这条不再报错，就说明锁可以重新评估；反过来，如果有人因为「看起来
        不需要」删掉锁，这条会告诉他为什么需要。
        """
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        tid = await _make_tenant(maker)
        day = date(2026, 3, 5)
        insert_sql = text(
            "INSERT INTO shop_daily_summary "
            "(id, tenant_id, stat_date, visitors, pay_amount, pay_orders, refreshed_at) "
            "VALUES (gen_random_uuid(), :t, :d, 1, 1, 1, NOW())"
        )
        delete_sql = text("DELETE FROM shop_daily_summary WHERE tenant_id = :t AND stat_date = :d")
        try:
            async with maker() as first, maker() as second:
                await first.execute(delete_sql, {"t": tid, "d": day})
                await first.execute(insert_sql, {"t": tid, "d": day})
                # second 的 DELETE 看不到 first 未提交的行，删了个寂寞
                await second.execute(delete_sql, {"t": tid, "d": day})
                await first.commit()
                # first 提交后，second 的 INSERT 撞上 first 插入的那一行
                with pytest.raises(Exception, match="(?i)unique|duplicate"):
                    await second.execute(insert_sql, {"t": tid, "d": day})
                await second.rollback()
        finally:
            await _drop_tenant(maker, tid)


class TestCoverage:
    async def test_every_day_in_range_is_recorded_even_without_data(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        """刷过的每一天都要有覆盖记录，**不论那天有没有数据**。

        覆盖记录要回答的就是「没有汇总行 = 没数据还是没刷过」。如果只给有数据的
        日子记，这个问题就又回到原点了。
        """
        await SummaryRefreshService(session).refresh(
            tenant_id=tenant_a.id, date_from=LO, date_to=HI
        )
        await session.commit()

        days = (
            (
                await session.execute(
                    text(
                        "SELECT stat_date FROM report_summary_coverage "
                        "WHERE tenant_id = :t ORDER BY stat_date"
                    ),
                    {"t": tenant_a.id},
                )
            )
            .scalars()
            .all()
        )
        assert days == [LO + timedelta(days=i) for i in range(31)]

    async def test_coverage_excludes_shop_only_extension(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        """店铺日表为了凑整桶多刷的那几天，不能算进覆盖。

        覆盖记录是读取侧「能不能信任汇总表」的依据，必须是 ROI / PR / 店铺三张日表
        **都**刷过的日子。只有店铺表刷过的日子记成覆盖，读取就会拿到半张空表。
        """
        lo, hi = date(2026, 3, 10), date(2026, 3, 12)
        await SummaryRefreshService(session).refresh(
            tenant_id=tenant_a.id, date_from=lo, date_to=hi
        )
        await session.commit()

        shop_lo, shop_hi = shop_daily_span(lo, hi)
        assert shop_lo < lo and shop_hi > hi, "测试前提：店铺区间确实被扩展了"

        recorded = (
            await session.execute(
                text(
                    "SELECT min(stat_date), max(stat_date), count(*) "
                    "FROM report_summary_coverage WHERE tenant_id = :t"
                ),
                {"t": tenant_a.id},
            )
        ).one()
        assert (recorded[0], recorded[1], recorded[2]) == (lo, hi, 3)

    async def test_repeat_refresh_updates_coverage_not_duplicates(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        svc = SummaryRefreshService(session)
        await svc.refresh(tenant_id=tenant_a.id, date_from=LO, date_to=HI)
        await session.commit()
        await svc.refresh(tenant_id=tenant_a.id, date_from=LO, date_to=HI)
        await session.commit()

        n = (
            await session.execute(
                text("SELECT count(*) FROM report_summary_coverage WHERE tenant_id = :t"),
                {"t": tenant_a.id},
            )
        ).scalar_one()
        assert n == 31
