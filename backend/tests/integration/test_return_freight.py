"""R33真实库事务：本机只收集，必须在CI的实际PG18上验。"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.tenancy import tenant_id_ctx
from app.modules.flow.line_events import LineEventType
from app.modules.promotion.exceptions import PromotionCompletedReadonlyError
from app.modules.promotion.freight_models import ReturnFreightPayment
from app.modules.promotion.freight_rules import FreightStateConflictError
from app.modules.promotion.freight_schemas import FreightResubmitRequest, FreightSubmitRequest
from app.modules.promotion.freight_service import ReturnFreightService
from app.modules.promotion.models import PromotionAmountLog
from app.modules.promotion.schemas import PromotionUpdate
from app.modules.promotion.service import PromotionService
from tests.flow_event_helpers import events_of

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def freight_tenant(tenant_a):
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest_asyncio.fixture
async def freight_promotion(
    session, flow_users, product_factory, blogger_factory, promotion_factory, attachment_factory
):
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    qr = await attachment_factory.attachment(purpose="promotion_payment_qr")
    waybill = await attachment_factory.attachment(purpose="recall_waybill_screenshot")
    p = await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=flow_users.pr,
        publish_status="已取消",
        recall_status="召回成功",
        settlement_status="未核查",
        return_shipping_fee=Decimal("30"),
        recall_waybill_attachment_id=waybill.id,
    )
    p.payment_qr_attachment_id = qr.id
    await session.flush()
    return p


async def test_t1_completed_without_freight_requires_star(session, freight_promotion, flow_users):
    p = freight_promotion
    p.completed_at = datetime.now(UTC)
    await session.flush()
    completed = p.completed_at
    service = ReturnFreightService(session)
    with pytest.raises(PromotionCompletedReadonlyError):
        await service.submit(p.id, FreightSubmitRequest(), flow_users.pr)
    f = await service.submit(p.id, FreightSubmitRequest(), flow_users.admin)
    assert f.status == "待审核" and f.amount == Decimal("30")
    assert p.return_shipping_payer == "博主垫付" and p.completed_at == completed
    with pytest.raises(FreightStateConflictError):
        await service.submit(p.id, FreightSubmitRequest(), flow_users.admin)
    count = await session.scalar(
        select(func.count())
        .select_from(ReturnFreightPayment)
        .where(ReturnFreightPayment.promotion_id == p.id)
    )
    assert count == 1


async def test_t6_completed_rejected_updates_both_amounts_and_snapshots(
    session, freight_promotion, flow_users, attachment_factory
):
    p = freight_promotion
    service = ReturnFreightService(session)
    f = await service.submit(p.id, FreightSubmitRequest(), flow_users.pr)
    original_id, old_qr, old_waybill = f.id, f.payment_qr_attachment_id, f.waybill_attachment_id
    p.completed_at = datetime.now(UTC)
    f.status = "已驳回"
    new_qr = await attachment_factory.attachment(purpose="promotion_payment_qr")
    new_waybill = await attachment_factory.attachment(purpose="recall_waybill_screenshot")
    p.payment_qr_attachment_id, p.recall_waybill_attachment_id = new_qr.id, new_waybill.id
    await session.flush()
    assert f.payment_qr_attachment_id == old_qr and f.waybill_attachment_id == old_waybill
    completed = p.completed_at
    updated = await service.resubmit(
        f.id, FreightResubmitRequest(note="更换收款码，运费核实为35元", amount="35"), flow_users.pr
    )
    assert (
        updated.id == original_id and updated.status == "待审核"
    ), "FLOW8_FAULT_EXPECTED:completed_resubmit"
    assert (
        updated.amount == p.return_shipping_fee == Decimal("35")
    ), "FLOW8_FAULT_EXPECTED:amount_atomic"
    assert (
        updated.payment_qr_attachment_id == new_qr.id
        and updated.waybill_attachment_id == new_waybill.id
    )
    assert p.completed_at == completed and p.return_shipping_payer == "博主垫付"
    logs = (
        (
            await session.execute(
                select(PromotionAmountLog).where(
                    PromotionAmountLog.promotion_id == p.id,
                    PromotionAmountLog.field_name == "return_shipping_fee",
                )
            )
        )
        .scalars()
        .all()
    )
    assert (
        len(logs) == 1
        and logs[0].before_value == Decimal("30")
        and logs[0].after_value == Decimal("35")
    )
    events = await events_of(session, line_id=p.line_id)
    submitted = [e for e in events if e.event_type == LineEventType.FREIGHT_SUBMITTED.value]
    assert {e.attachment_id for e in submitted} == {old_qr, old_waybill}


@pytest.mark.parametrize("status", ["待审核", "待付款", "已驳回", "已付款"])
@pytest.mark.parametrize("field", ["return_shipping_fee", "return_shipping_payer"])
async def test_existing_freight_rejects_fee_and_payer_patch_even_star(
    session, freight_promotion, flow_users, attachment_factory, status, field
):
    p = freight_promotion
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    if status == "已付款":
        from app.modules.promotion.urge_calculator import get_today

        proof = await attachment_factory.attachment()
        f.payment_date, f.payment_proof_attachment_id = get_today(), proof.id
    f.status = status
    await session.flush()
    value = "33" if field == "return_shipping_fee" else "到付"
    with pytest.raises(FreightStateConflictError) as exc:
        await PromotionService(session).update_promotion(
            p.id, PromotionUpdate(**{field: value}), flow_users.admin
        )
    assert exc.value.status_code == 409, "FLOW8_FAULT_EXPECTED:pending_fee_lock"
    assert (
        f.amount == p.return_shipping_fee == Decimal("30") and p.return_shipping_payer == "博主垫付"
    )


@pytest.mark.parametrize("who", ["pr", "pr_manager", "admin"])
@pytest.mark.parametrize("status", ["待审核", "待付款", "已付款"])
async def test_snapshot_states_lock_waybill_even_star(
    session, freight_promotion, flow_users, attachment_factory, who, status
):
    p = freight_promotion
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    if status == "已付款":
        from app.modules.promotion.urge_calculator import get_today

        proof = await attachment_factory.attachment()
        f.payment_date, f.payment_proof_attachment_id = get_today(), proof.id
    f.status = status
    await session.flush()
    with pytest.raises(FreightStateConflictError):
        await PromotionService(session)._recall_context(
            p.id, getattr(flow_users, who), waybill=True
        )
