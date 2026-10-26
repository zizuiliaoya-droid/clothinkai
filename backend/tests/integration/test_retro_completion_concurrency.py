"""真实独立连接：最后两步顺序无关，锁等待后的PATCH不可穿透完结门。"""

import asyncio
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppException
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import User
from app.modules.promotion.schemas import (
    PromotionMetricsRequest,
    PromotionUpdate,
    RetrospectiveWriteRequest,
)
from app.modules.promotion.service import PromotionService
from tests.integration.test_metrics_gate import _SHOT, _FakeS3

# 复用真实提交专用fixture与pg_blocking_pids探针；不使用全局savepoint session造并发数据。
from tests.integration.test_promotion_deactivate_concurrency import (  # noqa: F401
    _wait_for_blocker,
    session,
    tenant_a,
)
from tests.integration.test_settlement_routing import _pending_review

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("first", ["chat", "retro", "complete_then_patch", "metrics_again"])
async def test_last_steps_and_completed_gate_are_serialized(
    engine: Any,
    session: AsyncSession,  # noqa: F811 fixture以原名导入注册
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    monkeypatch: pytest.MonkeyPatch,
    first: str,
) -> None:
    from datetime import UTC, datetime

    from app.core.attachment import attachment_service

    monkeypatch.setattr(attachment_service, "_client", _FakeS3())
    promo = await _pending_review(
        flow_users,
        product_factory,
        blogger_factory,
        promotion_factory,
        settlement_status="已结款"
        if first in {"complete_then_patch", "metrics_again"}
        else "已付款",
        retro_status="待复盘",
        metrics_recorded_at=None if first == "metrics_again" else datetime.now(UTC),
    )
    pid, tid = promo.id, promo.tenant_id
    await session.commit()
    maker = async_sessionmaker(engine, expire_on_commit=False)
    held, release = asyncio.Event(), asyncio.Event()
    token = tenant_id_ctx.set(tid)
    tasks: list[asyncio.Task[Any]] = []
    try:
        async with maker() as first_s, maker() as second_s:
            first_pid = int(await first_s.scalar(text("SELECT pg_backend_pid()")))
            second_pid = int(await second_s.scalar(text("SELECT pg_backend_pid()")))
            assert first_pid != second_pid
            first_svc, second_svc = PromotionService(first_s), PromotionService(second_s)
            manager1 = await first_s.get(User, flow_users.pr_manager.id)
            manager2 = await second_s.get(User, flow_users.pr_manager.id)
            assert manager1 is not None and manager2 is not None
            update_method = "record_metrics_once" if first == "metrics_again" else "update_state"
            original = getattr(first_svc._repo, update_method)

            async def pause_after_update(**kw: Any) -> Any:
                updated = await original(**kw)
                assert updated is not None
                held.set()
                await asyncio.wait_for(release.wait(), 10)
                return updated

            monkeypatch.setattr(first_svc._repo, update_method, pause_after_update)

            async def action(svc: PromotionService, user: User, action_name: str) -> Any:
                try:
                    if action_name == "metrics":
                        return await svc.record_metrics(
                            pid,
                            PromotionMetricsRequest(
                                like_count=100, collect_count=20, comment_count=3
                            ),
                            user,
                            screenshot=_SHOT,
                        )
                    if action_name == "chat":
                        return await svc.upload_settlement_chat(
                            pid, user=user, filename=_SHOT[0], mime_type=_SHOT[1], data=_SHOT[2]
                        )
                    if action_name == "patch":
                        return await svc.update_promotion(
                            pid, PromotionUpdate(remark="不得穿透"), user
                        )
                    return await svc.write_retrospective(
                        pid, RetrospectiveWriteRequest(content="并发主管复盘"), user
                    )
                except AppException as exc:
                    await svc._session.rollback()
                    return exc

            first_action = (
                "metrics" if first == "metrics_again" else ("chat" if first == "chat" else "retro")
            )
            second_action = (
                "metrics"
                if first == "metrics_again"
                else (
                    "retro"
                    if first == "chat"
                    else ("patch" if first == "complete_then_patch" else "chat")
                )
            )
            tasks.append(asyncio.create_task(action(first_svc, manager1, first_action)))
            await asyncio.wait_for(held.wait(), 10)
            tasks.append(asyncio.create_task(action(second_svc, manager2, second_action)))
            await _wait_for_blocker(maker, tasks[1], second_pid, first_pid)
            release.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
            assert not isinstance(results[0], AppException)
            if first in {"complete_then_patch", "metrics_again"}:
                assert isinstance(results[1], AppException)
                assert results[1].status_code == 409
            else:
                assert not isinstance(results[1], AppException)
        async with maker() as check:
            row = (
                await check.execute(
                    text(
                        "SELECT completed_at,retro_status,settlement_status,remark, "
                        "(SELECT count(*) FROM line_event WHERE promotion_id=p.id AND event_type='promotion.completed') "
                        "FROM promotion p WHERE id=:pid"
                    ),
                    {"pid": pid},
                )
            ).one()
            if first == "metrics_again":
                assert row[0] is None and row[1:3] == ("待复盘", "已结款")
                assert row[4] == 0
                assert (
                    await check.scalar(
                        text(
                            "SELECT count(*) FROM line_event WHERE promotion_id=:pid AND event_type='promotion.metrics_recorded'"
                        ),
                        {"pid": pid},
                    )
                ) == 1
            else:
                assert row[0] is not None, "FLOW7_FAULT_EXPECTED:completion_order"
                assert row[1:3] == ("已完成", "已结款")
                assert row[3] != "不得穿透" and row[4] == 1
    finally:
        release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        tenant_id_ctx.reset(token)
