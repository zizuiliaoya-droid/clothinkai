"""独立连接同时重提/自动取消只允许一个结果；重复任务只写一个取消事件。"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import User
from app.modules.negotiation.rejection_cancel import cancel_expired_rejections
from app.modules.negotiation.schemas import NegotiationReviewRequest
from app.modules.negotiation.service import NegotiationService
from tests.concurrency import run_concurrently
from tests.conftest import purge_negotiations
from tests.integration.test_negotiation_two_level import world  # noqa: F401
from tests.integration.test_promotion_deactivate_concurrency import session, tenant_a  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("race", ["submit_vs_cancel", "duplicate_cancel"])
async def test_one_transition_wins(engine, session, flow_users, world, monkeypatch, race):  # noqa: F811
    from app.modules.negotiation import service as module

    anchor = datetime(2026, 11, 16, 2, tzinfo=UTC)
    monkeypatch.setattr(module, "_utcnow", lambda: anchor)
    tid, pr_id = flow_users.tenant.id, flow_users.pr.id
    token = tenant_id_ctx.set(tid)
    n = await world.make(flow_users.pr, "主管待审")
    nid, sku_id = n.id, world.sku.id
    try:
        await NegotiationService(session).review(
            nid,
            NegotiationReviewRequest(action="reject", review_opinion="并发原因"),
            flow_users.pr_manager,
        )
        await session.commit()
        # 请求在边界前已开始；定时扫描同时到达七天边界。
        monkeypatch.setattr(module, "_utcnow", lambda: anchor + timedelta(days=7, microseconds=-1))
        barrier = asyncio.Barrier(2)

        async def attempt(s, i):
            await asyncio.wait_for(barrier.wait(), 10)
            if i == 0 and race == "submit_vs_cancel":
                try:
                    await NegotiationService(s).submit(nid, await s.get(User, pr_id))
                    return "submitted"
                except AppException:
                    await s.rollback()
                    return "conflict"
            count = await cancel_expired_rejections(s, tid, anchor + timedelta(days=7))
            await s.commit()
            return "cancelled" if count else "unchanged"

        results = await run_concurrently(engine, 2, attempt, tenant_id=tid)
        assert results.count("submitted") + results.count("cancelled") == 1, results
        await session.rollback()
        status = await session.scalar(
            text("SELECT status FROM negotiation WHERE id=:id"), {"id": nid}
        )
        assert status == ("主管待审" if "submitted" in results else "已取消")
        changes = await session.scalar(
            text(
                "SELECT count(*) FROM line_event WHERE negotiation_id=:id AND event_type IN ('negotiation.submitted','negotiation.cancelled')"
            ),
            {"id": nid},
        )
        assert changes == 1
    finally:
        await session.rollback()
        await purge_negotiations(session, [nid])
        await session.execute(text("DELETE FROM sku WHERE id=:id"), {"id": sku_id})
        await session.commit()
        tenant_id_ctx.reset(token)
