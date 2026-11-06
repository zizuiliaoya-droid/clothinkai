"""同线留言分配seq前互斥，避免晚提交的低序号被阅读游标吞掉。"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import User
from app.modules.flow.timeline import LineEventService
from tests.integration.test_line_engagement import make_line
from tests.integration.test_promotion_deactivate_concurrency import (  # noqa: F401
    _wait_for_blocker,
    session,
    tenant_a,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_same_line_comment_seq_waits_for_previous_commit(
    engine,
    session,  # noqa: F811
    flow_users,
    product_factory,
    blogger_factory,
    promotion_factory,
    monkeypatch,
):
    promo = await make_line(product_factory, blogger_factory, promotion_factory, flow_users)
    line_id, tenant_id = promo.line_id, promo.tenant_id
    await session.commit()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    held, release = asyncio.Event(), asyncio.Event()
    tasks = []
    token = tenant_id_ctx.set(tenant_id)
    try:
        async with maker() as first, maker() as second:
            pid1 = await first.scalar(text("SELECT pg_backend_pid()"))
            pid2 = await second.scalar(text("SELECT pg_backend_pid()"))
            assert pid1 != pid2
            a, b = LineEventService(first), LineEventService(second)
            user1 = await first.get(User, flow_users.pr.id)
            user2 = await second.get(User, flow_users.pr_manager.id)
            original = a._events.append

            async def pause_after_seq(**kwargs):
                result = await original(**kwargs)
                held.set()
                await asyncio.wait_for(release.wait(), 10)
                return result

            monkeypatch.setattr(a._events, "append", pause_after_seq)
            tasks.append(asyncio.create_task(a.comment(line_id, user1, text="先到", image=None)))
            await asyncio.wait_for(held.wait(), 10)
            tasks.append(asyncio.create_task(b.comment(line_id, user2, text="后到", image=None)))
            await _wait_for_blocker(maker, tasks[1], pid2, pid1)
            release.set()
            first_event, second_event = await asyncio.wait_for(asyncio.gather(*tasks), 20)
            assert first_event.seq < second_event.seq
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tenant_id_ctx.reset(token)
