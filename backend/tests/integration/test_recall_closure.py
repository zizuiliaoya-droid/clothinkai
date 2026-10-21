"""C1–C5真实HTTP闭环；PG18执行，外部图片存储用记录型S3。"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.flow.line_events import LineEventType
from app.modules.promotion.freight_models import ReturnFreightPayment
from app.modules.promotion.models import PromotionAmountLog
from app.modules.promotion.schemas import RetrospectiveWriteRequest
from app.modules.promotion.service import PromotionService
from app.modules.urge.models import UrgeTask
from tests.flow_event_helpers import events_of
from tests.integration import test_return_freight as freight_fixtures
from tests.integration import test_settlement_proof_upload as storage_fixtures
from tests.integration.test_settlement_routing import _call

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
freight_promotion = freight_fixtures.freight_promotion
freight_tenant = freight_fixtures.freight_tenant
r2 = storage_fixtures.r2
_PNG = storage_fixtures._PNG


@pytest.mark.parametrize("ship", [None, "已发货", "待发货", "待打单"])
async def test_c2_unpublished_recall_cancels_and_closes_urge_atomically(
    session, freight_promotion, flow_users, ship
):
    p = freight_promotion
    p.publish_status, p.recall_status, p.ship_status = "未发布", "未召回", ship
    task = UrgeTask(
        tenant_id=p.tenant_id,
        promotion_id=p.id,
        blogger_id=p.blogger_id,
        pr_id=p.pr_id,
        status="进行中",
    )
    session.add(task)
    await session.flush()
    response = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/recall/start",
        {"recall_reason": "质量不符"},
    )
    await session.refresh(p)
    await session.refresh(task)
    if ship in ("待发货", "待打单"):
        assert response.status_code == 422 and "直接取消" in response.text
        assert (
            p.publish_status == "未发布" and p.recall_status == "未召回" and task.status == "进行中"
        )
        assert not await events_of(session, line_id=p.line_id)
    else:
        assert response.status_code == 200, response.text
        assert p.publish_status == "已取消" and p.recall_status == "召回中"
        assert p.cancel_reason == p.recall_reason == "质量不符"
        assert task.status == "已关闭" and task.close_reason == "已取消"
        events = await events_of(session, line_id=p.line_id)
        assert [e.event_type for e in events] == [
            "promotion.cancelled",
            "urge.closed",
            "recall.started",
        ]


@pytest.mark.parametrize("payer", ["博主垫付", "到付"])
@pytest.mark.parametrize("amount", ["0", "30"])
@pytest.mark.parametrize("has_qr", [False, True])
async def test_c4_fee_payer_qr_matrix(
    session, freight_promotion, flow_users, payer, amount, has_qr
):
    p = freight_promotion
    p.recall_status, p.return_shipping_fee = "召回中", None
    if not has_qr:
        p.payment_qr_attachment_id = None
    await session.flush()
    response = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/recall/success",
        {"return_shipping_fee": amount, "return_shipping_payer": payer, "remark": "已收回"},
    )
    needs_payment = payer == "博主垫付" and amount == "30"
    await session.refresh(p)
    rows = (
        await session.scalars(
            select(ReturnFreightPayment).where(ReturnFreightPayment.promotion_id == p.id)
        )
    ).all()
    if needs_payment and not has_qr:
        assert response.status_code == 422
        assert p.recall_status == "召回中" and p.completed_at is None and not rows
    else:
        assert response.status_code == 200, response.text
        assert p.completed_at is not None and p.recall_status == "召回成功"
        assert p.return_shipping_fee == Decimal(amount) and p.return_shipping_payer == payer
        assert len(rows) == int(needs_payment)
        if needs_payment:
            assert rows[0].status == "待审核" and rows[0].amount == Decimal("30")
        events = await events_of(session, line_id=p.line_id)
        succeeded = [e for e in events if e.event_type == "recall.succeeded"]
        assert len(succeeded) == 1 and succeeded[0].note == "已收回"


async def test_c4_requires_waybill_even_zero_due(session, freight_promotion, flow_users):
    p = freight_promotion
    p.recall_status, p.recall_waybill_attachment_id = "召回中", None
    await session.flush()
    response = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/recall/success",
        {"return_shipping_fee": "0", "return_shipping_payer": "到付"},
    )
    assert response.status_code == 422 and "寄回" in response.text
    await session.refresh(p)
    assert p.recall_status == "召回中" and p.completed_at is None


async def test_published_recall_does_not_complete_without_normal_requirements(
    session, freight_promotion, flow_users
):
    p = freight_promotion
    p.publish_status, p.recall_status = "已发布", "召回中"
    p.retro_status, p.metrics_recorded_at = "未开始", None
    await session.flush()
    response = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/recall/success",
        {"return_shipping_fee": "30", "return_shipping_payer": "博主垫付"},
    )
    assert response.status_code == 200, response.text
    await session.refresh(p)
    assert p.completed_at is None, "FLOW8_FAULT_EXPECTED:published_not_complete"
    f = await session.scalar(
        select(ReturnFreightPayment).where(ReturnFreightPayment.promotion_id == p.id)
    )
    assert f is not None and f.status == "待审核"


@pytest.mark.parametrize("result", ["success", "failure"])
async def test_published_recall_continues_after_last_retro_completes(
    session, freight_promotion, flow_users, r2, result
):
    p = freight_promotion
    p.publish_status, p.recall_status, p.settlement_status = "已发布", "召回中", "已结款"
    p.retro_status, p.metrics_recorded_at = "待复盘", datetime.now(UTC)
    await session.flush()
    await PromotionService(session).write_retrospective(
        p.id, RetrospectiveWriteRequest(content="正常流程最后一步复盘"), flow_users.pr_manager
    )
    await session.refresh(p)
    completed = p.completed_at
    assert completed is not None
    initial = await events_of(session, line_id=p.line_id)
    assert sum(e.event_type == LineEventType.PROMOTION_COMPLETED.value for e in initial) == 1
    upload = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/recall/waybill",
        files={"file": ("waybill.png", _PNG, "image/png")},
    )
    assert upload.status_code == 200, upload.text
    body = {"remark": "已处理"}
    if result == "success":
        body.update(return_shipping_fee="30", return_shipping_payer="博主垫付")
    response = await _call(
        session, flow_users.pr, "POST", f"/api/promotions/{p.id}/recall/{result}", body
    )
    assert response.status_code == 200, response.text
    await session.refresh(p)
    assert p.completed_at == completed
    events = await events_of(session, line_id=p.line_id)
    complete = [e for e in events if e.event_type == "promotion.completed"]
    assert len(complete) == 1 and complete[0].payload["completion_kind"] == "正常"
    f = await session.scalar(
        select(ReturnFreightPayment).where(ReturnFreightPayment.promotion_id == p.id)
    )
    assert (f is not None) is (result == "success"), "FLOW8_FAULT_EXPECTED:completed_auto_freight"
    assert (
        next(
            e
            for e in events
            if e.event_type == f"recall.{'succeeded' if result == 'success' else 'failed'}"
        ).note
        == "已处理"
    )
    for suffix, payload in (
        ("/recall/start", {"recall_reason": "不能再发起"}),
        ("", {"return_shipping_fee": "40"}),
    ):
        blocked = await _call(
            session,
            flow_users.pr,
            "POST" if suffix else "PATCH",
            f"/api/promotions/{p.id}{suffix}",
            payload,
        )
        assert blocked.status_code == 409


async def test_recall_failure_note_and_retry(session, freight_promotion, flow_users):
    p = freight_promotion
    p.recall_status = "召回中"
    await session.flush()
    response = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/recall/failure",
        {"remark": "物流拒收，重新联系"},
    )
    assert response.status_code == 200
    events = await events_of(session, line_id=p.line_id)
    assert events[-1].note == "物流拒收，重新联系"
    again = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/promotions/{p.id}/recall/start",
        {"recall_reason": "已联系新快递"},
    )
    assert again.status_code == 200 and again.json()["recall_status"] == "召回中"
    assert not (
        await session.scalars(
            select(PromotionAmountLog).where(PromotionAmountLog.promotion_id == p.id)
        )
    ).all()
