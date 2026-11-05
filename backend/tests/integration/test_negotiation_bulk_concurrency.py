"""并发相同行键只创建一次；批量收货确认复用原推广唯一约束。"""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import User
from app.modules.negotiation.bulk_schemas import BulkActionRequest, BulkCreateRequest
from app.modules.negotiation.bulk_service import BulkNegotiationService
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.schemas import NegotiationFinalReviewRequest, NegotiationReviewRequest
from app.modules.negotiation.service import NegotiationService
from tests.concurrency import run_concurrently
from tests.conftest import purge_negotiations
from tests.integration.test_negotiation_bulk import ready_payload
from tests.integration.test_promotion_deactivate_concurrency import session, tenant_a  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_same_key_concurrent_create_and_confirm_once(
    engine,
    session,  # noqa: F811
    flow_users,
    product_factory,
    blogger_factory,
    monkeypatch,
):
    tid, pr, manager, boss = (
        flow_users.tenant.id,
        flow_users.pr.id,
        flow_users.pr_manager.id,
        flow_users.admin.id,
    )
    payload = await ready_payload(product_factory, blogger_factory)
    key, group = uuid4(), uuid4()
    request = BulkCreateRequest(
        creation_group_id=group, items=[{"client_create_key": key, "payload": payload}]
    )
    await session.commit()
    barrier = asyncio.Barrier(2)
    original = BulkNegotiationService._existing

    async def synchronize_missing(service, user, row_key):
        found = await original(service, user, row_key)
        if found is None and row_key == key:
            await asyncio.wait_for(barrier.wait(), 10)
        return found

    monkeypatch.setattr(BulkNegotiationService, "_existing", synchronize_missing)

    async def create_one(s, _):
        return await BulkNegotiationService(s).create(request, await s.get(User, pr))

    token = tenant_id_ctx.set(tid)
    try:
        results = await run_concurrently(engine, 2, create_one, tenant_id=tid)
        assert all(result.success == 1 for result in results), [r.model_dump() for r in results]
        assert sum(result.results[0].reused for result in results) == 1
        ids = {r.results[0].negotiation_id for r in results}
        assert len(ids) == 1
        nid = ids.pop()
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM negotiation WHERE tenant_id=:tid AND client_create_key=:key"
                ),
                {"tid": tid, "key": key},
            )
            == 1
        )
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM line_event WHERE negotiation_id=:nid AND event_type='negotiation.created'"
                ),
                {"nid": nid},
            )
            == 1
        )
        service = NegotiationService(session)
        await service.submit(nid, await session.get(User, pr))
        await service.review(
            nid, NegotiationReviewRequest(action="approve"), await session.get(User, manager)
        )
        await service.final_review(
            nid, NegotiationFinalReviewRequest(action="approve"), await session.get(User, boss)
        )
        confirm = BulkActionRequest(
            items=[
                {
                    "negotiation_id": nid,
                    "payload": {
                        "receiver_name": "收件人",
                        "receiver_phone": "13800138000",
                        "receiver_address": "杭州地址",
                    },
                }
            ]
        )
        confirm_barrier = asyncio.Barrier(2)

        async def confirm_one(s, _):
            user = await s.get(User, pr)
            await asyncio.wait_for(confirm_barrier.wait(), 10)
            return await BulkNegotiationService(s).perform("confirm-receiver", confirm, user)

        confirmations = await run_concurrently(engine, 2, confirm_one, tenant_id=tid)
        assert sum(result.success for result in confirmations) == 1
        assert sum(result.skipped for result in confirmations) == 1
        assert sum(result.failed for result in confirmations) == 0
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM promotion WHERE line_id=(SELECT line_id FROM negotiation WHERE id=:id)"
                ),
                {"id": nid},
            )
            == 1
        )
    finally:
        await session.rollback()
        ids = (
            await session.scalars(
                select(Negotiation.id).where(
                    Negotiation.tenant_id == tid, Negotiation.creation_group_id == group
                )
            )
        ).all()
        await purge_negotiations(session, ids)
        # ready_payload为本测试建立的SKU；父fixture负责用户/款式等其余本租户造数。
        await session.execute(
            text("DELETE FROM sku WHERE id=:id"), {"id": UUID(payload["items"][0]["sku_id"])}
        )
        await session.commit()
        tenant_id_ctx.reset(token)
