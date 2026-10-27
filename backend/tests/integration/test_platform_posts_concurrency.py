"""主平台修改与其他平台替换必须共用父推广单行锁。"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import User
from app.modules.promotion.schemas import PlatformPostIn, PromotionUpdate
from app.modules.promotion.service import PromotionService
from tests.integration.test_promotion_content_platform import promotion
from tests.integration.test_promotion_deactivate_concurrency import (  # noqa: F401
    _attempt,
    _wait_for_blocker,
    session,
    tenant_a,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_primary_change_serializes_with_platform_replacement(
    engine,
    session,  # noqa: F811
    flow_users,
    product_factory,
    blogger_factory,
    promotion_factory,
    monkeypatch,
):
    p = await promotion(product_factory, blogger_factory, promotion_factory, flow_users)
    pid, tid = p.id, p.tenant_id
    await session.commit()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    held, release = asyncio.Event(), asyncio.Event()
    tasks = []
    token = tenant_id_ctx.set(tid)
    try:
        async with maker() as first, maker() as second:
            first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
            assert first_pid != second_pid
            user1, user2 = (
                await first.get(User, flow_users.admin.id),
                await second.get(User, flow_users.admin.id),
            )
            a, b = PromotionService(first), PromotionService(second)
            original = a._repo.get_for_write

            async def hold(*args, **kwargs):
                result = await original(*args, **kwargs)
                held.set()
                await asyncio.wait_for(release.wait(), 10)
                return result

            monkeypatch.setattr(a._repo, "get_for_write", hold)
            tasks.append(
                asyncio.create_task(
                    _attempt(
                        first,
                        lambda: a.update_promotion(pid, PromotionUpdate(platform="抖音"), user1),
                    )
                )
            )
            await asyncio.wait_for(held.wait(), 10)
            tasks.append(
                asyncio.create_task(
                    _attempt(
                        second,
                        lambda: b.replace_platform_posts(
                            pid, [PlatformPostIn(platform="抖音")], user2
                        ),
                    )
                )
            )
            await _wait_for_blocker(maker, tasks[1], second_pid, first_pid)
            release.set()
            result = await asyncio.wait_for(asyncio.gather(*tasks), 15)
            assert not isinstance(result[0], AppException)
            assert isinstance(result[1], AppException) and result[1].status_code == 422
        async with maker() as check:
            assert (
                await check.scalar(text("SELECT platform FROM promotion WHERE id=:id"), {"id": pid})
                == "抖音"
            )
            assert (
                await check.scalar(
                    text("SELECT count(*) FROM promotion_platform_post WHERE promotion_id=:id"),
                    {"id": pid},
                )
                == 0
            )
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tenant_id_ctx.reset(token)
