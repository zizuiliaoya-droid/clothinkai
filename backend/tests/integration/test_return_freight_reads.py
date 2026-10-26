"""R33真实HTTP、真实角色/金额裁剪和无服务费副作用；对象存储只替换外部S3。"""

from datetime import date
from decimal import Decimal

import pytest
from freezegun import freeze_time
from sqlalchemy import select

from app.core.events import subscribe
from app.core.tenancy import tenant_id_ctx
from app.modules.finance.events import SettlementPaid
from app.modules.flow.models import LineEvent
from app.modules.flow.timeline import LineEventService
from app.modules.promotion.freight_schemas import FreightListFilters, FreightSubmitRequest
from app.modules.promotion.freight_service import ReturnFreightService
from app.modules.promotion.listeners import on_settlement_paid
from tests.integration import test_return_freight as freight_fixtures
from tests.integration import test_settlement_proof_upload as storage_fixtures
from tests.integration.test_settlement_routing import _call

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
freight_promotion = freight_fixtures.freight_promotion
freight_tenant = freight_fixtures.freight_tenant
r2 = storage_fixtures.r2
_PNG = storage_fixtures._PNG


async def test_freight_paid_does_not_advance_service_settlement(
    session, freight_promotion, flow_users, r2
):
    p = freight_promotion
    # 真实listener有可推进的推广状态；若错误发SettlementPaid会把待付款改成已付款。
    p.settlement_status = "待付款"
    await session.flush()
    await session.refresh(p)
    original_cost = p.total_promo_cost
    emitted: list[SettlementPaid] = []

    async def capture(event, session):
        emitted.append(event)

    subscribe("SettlementPaid", on_settlement_paid)
    subscribe("SettlementPaid", capture)
    submit = await _call(
        session, flow_users.pr, "POST", f"/api/promotions/{p.id}/return-freight", {}
    )
    assert submit.status_code == 200, submit.text
    fid = submit.json()["id"]
    denied = await _call(
        session,
        flow_users.pr,
        "POST",
        f"/api/return-freight-payments/{fid}/pay",
        files={"file": ("pay.png", _PNG, "image/png")},
    )
    assert denied.status_code == 403
    approved = await _call(
        session,
        flow_users.pr_manager,
        "POST",
        f"/api/return-freight-payments/{fid}/review",
        {"action": "approve"},
    )
    assert approved.status_code == 200, approved.text
    missing = await _call(
        session, flow_users.finance, "POST", f"/api/return-freight-payments/{fid}/pay"
    )
    assert missing.status_code == 422
    with freeze_time("2026-05-26 16:30:00"):
        paid = await _call(
            session,
            flow_users.finance,
            "POST",
            f"/api/return-freight-payments/{fid}/pay",
            files={"file": ("pay.png", _PNG, "image/png")},
        )
    assert paid.status_code == 200, paid.text
    assert paid.json()["payment_date"] == "2026-05-27" and paid.json()["status"] == "已付款"
    await session.refresh(p)
    assert (
        p.settlement_status == "待付款" and not emitted
    ), "FLOW8_FAULT_EXPECTED:no_service_payment"
    assert p.return_shipping_fee == Decimal("30")
    assert (
        p.total_promo_cost
        == original_cost
        == p.quote_amount + (p.cost_snapshot or Decimal("0")) + Decimal("30")
    )
    events = (await session.scalars(select(LineEvent).where(LineEvent.line_id == p.line_id))).all()
    assert sum(e.event_type == "freight.paid" for e in events) == 1
    assert not any(
        e.event_type == "settlement.paid" for e in events
    ), "FLOW8_FAULT_EXPECTED:no_service_payment"
    again = await _call(
        session,
        flow_users.finance,
        "POST",
        f"/api/return-freight-payments/{fid}/pay",
        files={"file": ("pay.png", _PNG, "image/png")},
    )
    assert again.status_code == 409 and len(r2.puts) == 1


@pytest.mark.parametrize("who", ["pr_manager", "admin"])
@pytest.mark.parametrize("actual_negotiator", [False, True])
async def test_freight_self_review_is_forbidden_even_star(
    session,
    freight_promotion,
    flow_users,
    negotiation_factory,
    product_factory,
    blogger_factory,
    who,
    actual_negotiator,
):
    p, user = freight_promotion, getattr(flow_users, who)
    if actual_negotiator:
        style, blogger = await product_factory.style(), await blogger_factory.blogger()
        await negotiation_factory.negotiation(
            style=style,
            blogger=blogger,
            pr=user,
            status="已定稿",
            promotion_id=p.id,
            line_id=p.line_id,
        )
    else:
        p.pr_id = user.id
    await session.flush()
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    response = await _call(
        session, user, "POST", f"/api/return-freight-payments/{f.id}/review", {"action": "approve"}
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "FLOW_ACTION_FORBIDDEN"
    await session.refresh(f)
    assert f.status == "待审核"


async def test_single_read_clips_before_signing_and_finance_freight_only_timeline(
    session, freight_promotion, flow_users, monkeypatch
):
    from app.core.attachment import attachment_service

    p = freight_promotion
    service = ReturnFreightService(session)
    f = await service.submit(p.id, FreightSubmitRequest(), flow_users.pr)
    signed = []

    def sign(bucket, key, **kw):
        signed.append(key)
        return f"https://fake.invalid/{key}"

    monkeypatch.setattr(attachment_service, "get_signed_url", sign)
    result = await _call(
        session, flow_users.operations, "GET", f"/api/return-freight-payments/{f.id}"
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["amount"] is None and not signed
    assert all(
        body[k] is None
        for k in (
            "payment_qr_attachment_id",
            "waybill_attachment_id",
            "payment_proof_attachment_id",
            "payment_qr_signed_url",
            "waybill_signed_url",
            "payment_proof_signed_url",
        )
    )
    timeline = await LineEventService(session).read(p.line_id, flow_users.operations)
    assert not signed
    for event in timeline.events:
        if event.event_type.startswith("freight."):
            assert "freight_amount" not in event.payload and event.attachment is None
    finance = await _call(session, flow_users.finance, "GET", f"/api/lines/{p.line_id}/events")
    assert finance.status_code == 200, finance.text
    assert finance.json()["promotion"] is not None
    assert (
        sum(event["event_type"] == "freight.submitted" for event in finance.json()["events"]) == 2
    )
    assert len(signed) == 2
    assert (
        await _call(session, flow_users.pr, "GET", "/api/return-freight-payments/")
    ).status_code == 403
    assert (
        await _call(session, flow_users.pr, "GET", f"/api/return-freight-payments/{f.id}")
    ).status_code == 200
    assert (
        await _call(session, flow_users.warehouse, "GET", f"/api/return-freight-payments/{f.id}")
    ).status_code == 404


async def test_default_manager_finance_list_is_read_only(session, freight_promotion, flow_users):
    """真实默认角色读取财务列表；主管审核在推广页，财务支付权限没有临时补给。"""
    p = freight_promotion
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    page = await _call(session, flow_users.pr_manager, "GET", "/api/return-freight-payments/")
    assert page.status_code == 200, page.text
    item = next(item for item in page.json()["items"] if item["id"] == str(f.id))
    assert item["ui"]["actions"]["freight_review"]["state"] == "enabled"
    assert "freight_pay" not in item["ui"]["actions"]
    for suffix, body in (("pay", None), ("reject-payment", {"reason": "无付款权限"})):
        denied = await _call(
            session,
            flow_users.pr_manager,
            "POST",
            f"/api/return-freight-payments/{f.id}/{suffix}",
            body,
        )
        assert denied.status_code == 403, denied.text


@pytest.mark.parametrize("who", ["pr_manager", "admin"])
async def test_missing_owner_can_be_read_but_never_reviewed(
    session, freight_promotion, flow_users, who
):
    p = freight_promotion
    p.pr_id = None
    await session.flush()
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    user = getattr(flow_users, who)
    read = await _call(session, user, "GET", f"/api/return-freight-payments/{f.id}")
    assert read.status_code == 200, read.text
    assert read.json()["ui"]["actions"]["freight_review"]["state"] == "disabled"
    rejected = await _call(
        session, user, "POST", f"/api/return-freight-payments/{f.id}/review", {"action": "approve"}
    )
    assert (
        rejected.status_code == 403 and rejected.json()["details"]["rule"] == "review_owner_missing"
    ), rejected.text


async def test_freight_list_filters_and_tenant_hidden(
    session, freight_promotion, flow_users, tenant_b, factory, pr_role
):
    p = freight_promotion
    service = ReturnFreightService(session)
    f = await service.submit(p.id, FreightSubmitRequest(), flow_users.pr)
    filters = FreightListFilters(status="待审核", promotion_id=p.id, keyword=p.internal_code)
    page = await service.page(filters, page=1, page_size=1, user=flow_users.finance)
    assert page.total == 1 and page.items[0].id == f.id
    second = await service.page(filters, page=2, page_size=1, user=flow_users.finance)
    assert second.total == 1 and not second.items
    paid = await service.page(
        FreightListFilters(payment_date_from=date(2026, 1, 1)),
        page=1,
        page_size=20,
        user=flow_users.finance,
    )
    assert not paid.items
    token = tenant_id_ctx.set(tenant_b.id)
    try:
        # 显式tenant与实际HTTP身份各自拒绝；真正应用角色RLS另由专用文件验证。
        assert await service.freights.get(f.id, tenant_id=tenant_b.id) is None
        other = await factory.user(tenant_b, roles=[pr_role])
        hidden = await _call(session, other, "GET", f"/api/return-freight-payments/{f.id}")
        assert hidden.status_code == 404, hidden.text
    finally:
        tenant_id_ctx.reset(token)


async def test_promotion_list_freight_summary_is_batched_and_clipped(
    session, freight_promotion, flow_users, product_factory, blogger_factory, promotion_factory
):
    from sqlalchemy import event

    p = freight_promotion
    await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    style, blogger = await product_factory.style(), await blogger_factory.blogger()
    await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
    engine = session.get_bind().engine
    queries = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if (
            statement.lstrip().lower().startswith("select")
            and "return_freight_payment" in statement
        ):
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = await _call(session, flow_users.operations, "GET", "/api/promotions/")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    rows = response.json()["items"]
    assert len(rows) >= 2
    result = next(row for row in rows if row["id"] == str(p.id))
    assert (
        result["freight_payment"]["status"] == "待审核"
        and result["freight_payment"]["amount"] is None
    )
    assert (
        result["recall_waybill_attachment_id"] is None
        and result["recall_waybill_signed_url"] is None
    )
    assert len(queries) == 1, "每页只批量读取一次R33，不按推广逐行查询"


async def test_review_reject_and_finance_reject_require_reason_and_keep_id(
    session, freight_promotion, flow_users
):
    p = freight_promotion
    f = await ReturnFreightService(session).submit(p.id, FreightSubmitRequest(), flow_users.pr)
    path = f"/api/return-freight-payments/{f.id}"
    blank = await _call(
        session,
        flow_users.pr_manager,
        "POST",
        f"{path}/review",
        {"action": "reject", "reason": "   "},
    )
    assert blank.status_code == 422
    rejected = await _call(
        session,
        flow_users.pr_manager,
        "POST",
        f"{path}/review",
        {"action": "reject", "reason": "请核对运费"},
    )
    assert rejected.status_code == 200 and rejected.json()["status"] == "已驳回"
    resubmit = await _call(
        session, flow_users.pr, "POST", f"{path}/resubmit", {"note": "已核对", "amount": "32.50"}
    )
    assert resubmit.status_code == 200 and resubmit.json()["id"] == str(f.id)
    approved = await _call(
        session, flow_users.pr_manager, "POST", f"{path}/review", {"action": "approve"}
    )
    assert approved.status_code == 200
    blank_finance = await _call(
        session, flow_users.finance, "POST", f"{path}/reject-payment", {"reason": ""}
    )
    assert blank_finance.status_code == 422
    reject = await _call(
        session, flow_users.finance, "POST", f"{path}/reject-payment", {"reason": "码需更新"}
    )
    assert reject.status_code == 200 and reject.json()["status"] == "已驳回"
    assert reject.json()["reject_reason"] == "码需更新"
