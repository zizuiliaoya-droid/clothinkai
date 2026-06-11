"""U05 并发守护测试（FB7）。

跨连接并发测试需要"真实已提交"数据对多个连接可见，故本文件不使用
rollback 隔离的 ``session`` fixture，而是用独立的 committed 数据 + 显式清理
（``_committed_session`` + finally DELETE）。并发度压到 30 以内避免
PostgreSQL ``max_connections`` 上限。

- next_settlement_sequence 30 并发首次 → 序号无重复
- update_state 30 并发推进同 settlement → 1 成功其余冲突
- update_state 跨租户 / 错误旧状态 → 0 行匹配
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import tenant_id_ctx
from app.modules.finance.repository import SettlementRepository


@pytest.mark.integration
@pytest.mark.asyncio
class TestSequenceConcurrent:
    """FB2: 首次创建 race 也无重复（INSERT ON CONFLICT DO UPDATE RETURNING）."""

    async def test_concurrent_first_create_no_duplicates(
        self, engine: Any
    ) -> None:
        Session = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        date_key = date(2026, 5, 26)

        # 用 003 seed 的已提交默认 tenant（settlement_sequence FK → tenant）
        async with Session() as s0:
            tenant_row = (
                await s0.execute(
                    text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1")
                )
            ).first()
            assert tenant_row is not None, "默认 tenant 缺失（003 seed 未跑）"
            tenant_id = tenant_row[0]

        # 前置清理：删除可能残留的 sequence 行（防上一轮失败未清理导致起点 != 1）
        async with Session() as pre:
            await pre.execute(
                text(
                    "DELETE FROM settlement_sequence "
                    "WHERE tenant_id = :tid AND date_key = :dk"
                ),
                {"tid": tenant_id, "dk": date_key},
            )
            await pre.commit()

        async def fetch_one() -> int:
            async with Session() as s:
                token = tenant_id_ctx.set(tenant_id)
                try:
                    repo = SettlementRepository(s)
                    seq = await repo.next_settlement_sequence(
                        tenant_id=tenant_id, date_key=date_key
                    )
                    await s.commit()
                    return seq
                finally:
                    tenant_id_ctx.reset(token)

        try:
            results = await asyncio.gather(*[fetch_one() for _ in range(30)])
            # FB2 核心断言：30 并发分配的序号互不重复且连续
            assert len(set(results)) == 30, f"序号重复: {sorted(results)}"
            assert sorted(results) == list(range(1, 31))
        finally:
            # 清理 committed sequence 行
            async with Session() as cleanup:
                await cleanup.execute(
                    text(
                        "DELETE FROM settlement_sequence "
                        "WHERE tenant_id = :tid AND date_key = :dk"
                    ),
                    {"tid": tenant_id, "dk": date_key},
                )
                await cleanup.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdateStateConcurrent:
    """FB7: UPDATE WHERE old_state RETURNING 保证只 1 个成功."""

    async def test_concurrent_state_transition_only_one_succeeds(
        self,
        engine: Any,
    ) -> None:
        """30 并发 待付款→待财务付款 同 settlement → 1 成功其余冲突。

        全自包含 committed 数据（用 003 seed 的默认 tenant）+ finally 清理，
        不依赖 rollback fixture（跨连接需真实可见）。
        """
        Session = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        settlement_id = uuid4()
        promotion_id = uuid4()
        style_id = uuid4()
        blogger_id = uuid4()
        suffix = uuid4().hex[:8]

        # 1) seed：默认 tenant + style + blogger + promotion + settlement（已提交）
        async with Session() as seed:
            tenant_row = (
                await seed.execute(
                    text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1")
                )
            ).first()
            assert tenant_row is not None, "默认 tenant 缺失（003 seed 未跑）"
            tenant_id = tenant_row[0]

            await seed.execute(
                text(
                    "INSERT INTO style (id, tenant_id, style_code, style_name, "
                    "category, design_status, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :code, 'CC款', '连衣裙', '大货', true, false, NOW(), NOW())"
                ),
                {"id": style_id, "tid": tenant_id, "code": f"CCST{suffix}"},
            )
            await seed.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, "
                    "platform, is_suspected_fake, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :xhs, 'CC博主', '小红书', false, true, false, NOW(), NOW())"
                ),
                {"id": blogger_id, "tid": tenant_id, "xhs": f"XHS{suffix}"},
            )
            await seed.execute(
                text(
                    "INSERT INTO promotion (id, tenant_id, style_id, blogger_id, "
                    "internal_code, style_code_snapshot, style_short_name_snapshot, "
                    "quote_amount, platform, cooperation_date, publish_status, "
                    "recall_status, settlement_status, is_active, created_at, updated_at) "
                    "VALUES (:id, :tid, :sid, :bid, :code, 'SC', 'SN', 500.00, "
                    "'小红书', :cd, '已发布', '未召回', '待付款', true, NOW(), NOW())"
                ),
                {
                    "id": promotion_id,
                    "tid": tenant_id,
                    "sid": style_id,
                    "bid": blogger_id,
                    "code": f"CCPR{suffix}",
                    "cd": date(2026, 5, 26),
                },
            )
            await seed.execute(
                text(
                    "INSERT INTO settlement (id, tenant_id, promotion_id, blogger_id, "
                    "style_id, settlement_no, amount, total_amount, settlement_status, "
                    "request_event_id, created_at, updated_at) "
                    "VALUES (:id, :tid, :pid, :bid, :sid, :no, 500.00, 500.00, "
                    "'待付款', :eid, NOW(), NOW())"
                ),
                {
                    "id": settlement_id,
                    "tid": tenant_id,
                    "pid": promotion_id,
                    "bid": blogger_id,
                    "sid": style_id,
                    "no": f"CCSE{suffix}",
                    "eid": uuid4(),
                },
            )
            await seed.commit()

        async def attempt() -> str:
            async with Session() as sess:
                tok2 = tenant_id_ctx.set(tenant_id)
                try:
                    repo = SettlementRepository(sess)
                    updated = await repo.update_state(
                        settlement_id=settlement_id,
                        tenant_id=tenant_id,
                        from_state_value="待付款",
                        to_state_value="待财务付款",
                        extra_fields={"payment_amount": Decimal("100.00")},
                    )
                    await sess.commit()
                    return "ok" if updated is not None else "conflict"
                finally:
                    tenant_id_ctx.reset(tok2)

        try:
            results = await asyncio.gather(*[attempt() for _ in range(30)])
            assert sum(1 for r in results if r == "ok") == 1
        finally:
            async with Session() as cleanup:
                await cleanup.execute(
                    text("DELETE FROM settlement WHERE id = :id"),
                    {"id": settlement_id},
                )
                await cleanup.execute(
                    text("DELETE FROM promotion WHERE id = :id"),
                    {"id": promotion_id},
                )
                await cleanup.execute(
                    text("DELETE FROM blogger WHERE id = :id"),
                    {"id": blogger_id},
                )
                await cleanup.execute(
                    text("DELETE FROM style WHERE id = :id"),
                    {"id": style_id},
                )
                await cleanup.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpdateStateNoMatch:
    """FB7: tenant / from_state 不匹配 → 0 行匹配（rollback session 即可）."""

    async def test_cross_tenant_update_state_no_match(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, settlement_status="待付款",
            )
            repo = SettlementRepository(session)
            updated = await repo.update_state(
                settlement_id=s.id,
                tenant_id=tenant_b.id,  # 错误租户
                from_state_value="待付款",
                to_state_value="待财务付款",
            )
            assert updated is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_update_state_wrong_from_state_no_match(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, settlement_status="待核查",
            )
            repo = SettlementRepository(session)
            updated = await repo.update_state(
                settlement_id=s.id,
                tenant_id=tenant_a.id,
                from_state_value="待财务付款",  # 实际是待核查
                to_state_value="已付款",
            )
            assert updated is None
        finally:
            tenant_id_ctx.reset(token)
