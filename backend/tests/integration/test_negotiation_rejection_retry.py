"""原单重提、多轮驳回、旧单保留、到期取消和事件事务。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from freezegun import freeze_time
from sqlalchemy import text

from app.modules.flow.line_events import LineEventRepository
from app.modules.negotiation.exceptions import NegotiationStateConflictError
from app.modules.negotiation.rejection_cancel import cancel_expired_rejections
from app.modules.negotiation.schemas import NegotiationReviewRequest, NegotiationUpdate
from app.modules.negotiation.service import NegotiationService
from tests.integration.test_negotiation_two_level import tenant_ctx, world  # noqa: F401

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.usefixtures("tenant_ctx")]
T0 = datetime(2026, 11, 16, 2, tzinfo=UTC)


async def test_original_line_retains_multiple_rejections_after_approval(session, flow_users, world):  # noqa: F811
    n = await world.make(flow_users.pr, "主管待审")
    nid, line_id = n.id, n.line_id
    service = NegotiationService(session)
    for day, reason in [(0, "报价过高"), (2, "档期需要调整")]:
        with freeze_time(T0 + timedelta(days=day)):
            await service.review(
                nid,
                NegotiationReviewRequest(action="reject", review_opinion=reason),
                flow_users.pr_manager,
            )
        with freeze_time(T0 + timedelta(days=day, hours=1)):
            edited = await service.update(
                nid, NegotiationUpdate(quote_amount=Decimal("200")), flow_users.pr
            )
            assert edited.status == "审核驳回" and edited.review_opinion == reason
            resubmitted = await service.submit(nid, flow_users.pr)
            assert (resubmitted.id, resubmitted.line_id, resubmitted.status) == (
                nid,
                line_id,
                "主管待审",
            )
            assert resubmitted.reviewed_by is None and resubmitted.review_opinion is None
    with freeze_time(T0 + timedelta(days=3)):
        await service.review(nid, NegotiationReviewRequest(action="approve"), flow_users.pr_manager)
    events = await LineEventRepository(session).list_by_line(tenant_id=n.tenant_id, line_id=line_id)
    assert [e.note for e in events if e.event_type == "negotiation.manager_rejected"] == [
        "报价过高",
        "档期需要调整",
    ]
    history = await service.blogger_history(world.blogger.id, flow_users.pr)
    assert [r.opinion for r in history.rejections] == ["档期需要调整", "报价过高"]
    assert history.total_rejections == 2
    assert (
        await session.scalar(
            text("SELECT count(*) FROM negotiation WHERE line_id=:l"), {"l": line_id}
        )
        == 1
    )


async def test_legacy_edit_pins_anchor_and_resubmit_preserves_real_reason(
    session,
    flow_users,
    world,  # noqa: F811
):
    n = await world.make(
        flow_users.pr,
        "审核驳回",
        reviewed_at=None,
        updated_at=T0,
        reviewed_by=flow_users.pr_manager.id,
        review_opinion="旧单价格过高",
    )
    service = NegotiationService(session)
    with freeze_time(T0 + timedelta(days=2)):
        edited = await service.update(n.id, NegotiationUpdate(remark="重新沟通"), flow_users.pr)
        assert edited.reviewed_at == T0
    with freeze_time(T0 + timedelta(days=6)):
        await service.submit(n.id, flow_users.pr)
    events = await LineEventRepository(session).list_by_line(
        tenant_id=n.tenant_id, line_id=n.line_id
    )
    rejections = [e for e in events if e.event_type == "negotiation.manager_rejected"]
    assert len(rejections) == 1
    assert (rejections[0].note, rejections[0].created_at, rejections[0].actor_id) == (
        "旧单价格过高",
        T0,
        flow_users.pr_manager.id,
    )


async def test_expiry_boundary_is_idempotent_and_never_cancels_other_states(
    session,
    flow_users,
    world,  # noqa: F811
):
    expired = await world.make(flow_users.pr, "审核驳回", reviewed_at=T0, review_opinion="超期原因")
    fresh = await world.make(flow_users.pr, "审核驳回", reviewed_at=T0 + timedelta(seconds=1))
    pending = await world.make(flow_users.pr, "主管待审", reviewed_at=T0)
    deadline = T0 + timedelta(days=7)
    assert (
        await cancel_expired_rejections(
            session, expired.tenant_id, deadline - timedelta(microseconds=1)
        )
        == 0
    )
    service = NegotiationService(session)
    with freeze_time(deadline):
        with pytest.raises(NegotiationStateConflictError):
            await service.submit(expired.id, flow_users.pr)
        with pytest.raises(NegotiationStateConflictError):
            await service.update(expired.id, NegotiationUpdate(remark="不能续期"), flow_users.pr)
    assert await cancel_expired_rejections(session, expired.tenant_id, deadline) == 1
    assert await cancel_expired_rejections(session, expired.tenant_id, deadline) == 0
    await session.commit()
    assert (await service.get(expired.id, flow_users.pr)).status == "已取消"
    assert (await service.get(fresh.id, flow_users.pr)).status == "审核驳回"
    assert (await service.get(pending.id, flow_users.pr)).status == "主管待审"
    events = await LineEventRepository(session).list_by_line(
        tenant_id=expired.tenant_id, line_id=expired.line_id
    )
    assert [(e.event_type, e.actor_id) for e in events] == [("negotiation.cancelled", None)]


async def test_cancel_event_failure_rolls_back_state(session, flow_users, world, monkeypatch):  # noqa: F811
    n = await world.make(flow_users.pr, "审核驳回", reviewed_at=T0)
    nid, tid = n.id, n.tenant_id

    async def fail_event(*args, **kwargs):
        raise RuntimeError("injected event failure")

    monkeypatch.setattr(LineEventRepository, "append", fail_event)
    with pytest.raises(RuntimeError, match="injected event failure"):
        async with session.begin_nested():
            await cancel_expired_rejections(session, tid, T0 + timedelta(days=7))
    assert (
        await session.scalar(text("SELECT status FROM negotiation WHERE id=:id"), {"id": nid})
        == "审核驳回"
    )
    assert (
        await session.scalar(
            text("SELECT count(*) FROM line_event WHERE negotiation_id=:id"), {"id": nid}
        )
        == 0
    )
