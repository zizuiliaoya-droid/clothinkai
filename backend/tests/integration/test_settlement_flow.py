"""流程线 PR-6：结款单 F0 ~ F4（设计 3.4、7.5、9.3 D「结款单」行）。

- F1：``SettlementRequested`` 建单直接落在「待付款」，同事务写 ``settlement.created``
- F0：只给上线前停在「待核查」的旧单走完；新单（待付款）approve → 422
- F2：增加结算项「运费」停用 → 422
- F3：实付 ≥ 0；实付 ≠ 应付（``total_amount``）时调整说明必填（422 ``FLOW_GATE_MISSING``）
- F4：付款日期一律取北京时间的当天，客户端传的日期忽略（D5：容器是 UTC）
- 每一步各写一条结款组事件（6.3），付款事件的附件 = 付款凭证
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from freezegun import freeze_time
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IllegalStateTransitionError
from app.core.tenancy import tenant_id_ctx
from app.modules.finance.enums import ExtraItemType
from app.modules.finance.exceptions import ExtraItemNotAllowedError
from app.modules.finance.listeners import on_settlement_requested
from app.modules.finance.models import Settlement, SettlementExtraItem
from app.modules.finance.schemas import (
    SettlementExtraItemCreateRequest,
    SettlementPaymentAmountRequest,
    SettlementPaymentProofRequest,
    SettlementReviewRequest,
)
from app.modules.finance.service import SettlementService
from app.modules.flow.exceptions import FlowGateMissingError
from app.modules.flow.line_events import LineEventType
from app.modules.flow.models import LineEvent
from app.modules.promotion.enums import ReviewAction
from app.modules.promotion.events import SettlementRequested
from app.modules.promotion.urge_calculator import get_today
from tests.flow_event_helpers import assert_payload_registered
from tests.integration.test_settlement_proof_upload import _PNG, _RecordingS3

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# UTC 05-26 16:30 = 北京 05-27 00:30：容器的 date.today() 还是 26 日（同 7a 的边界）
_FROZEN_UTC = "2026-05-26 16:30:00"
_BEIJING_TODAY = date(2026, 5, 27)


@pytest.fixture
def r2(monkeypatch: pytest.MonkeyPatch) -> _RecordingS3:
    from app.core import attachment as att_mod

    fake = _RecordingS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


async def _settlement(
    settlement_factory: Any,
    product_factory: Any,
    blogger_factory: Any,
    *,
    status: str,
    **kw: Any,
) -> Settlement:
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    return await settlement_factory.settlement(
        style=style, blogger=blogger, settlement_status=status, **kw
    )


async def _line_id(session: AsyncSession, promotion_id: UUID) -> UUID:
    row = await session.execute(
        sa_text("SELECT line_id FROM promotion WHERE id = :id"), {"id": promotion_id}
    )
    return UUID(str(row.scalar_one()))


async def _events(
    session: AsyncSession, promotion_id: UUID, event_type: LineEventType
) -> list[LineEvent]:
    stmt = (
        select(LineEvent)
        .where(LineEvent.promotion_id == promotion_id, LineEvent.event_type == event_type.value)
        .order_by(LineEvent.seq)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _status(session: AsyncSession, settlement_id: UUID) -> str:
    row = await session.execute(
        sa_text("SELECT settlement_status FROM settlement WHERE id = :id"), {"id": settlement_id}
    )
    return str(row.scalar_one())


def _request(promotion: Any, *, requested_by: UUID, line_id: UUID | None) -> SettlementRequested:
    now = datetime.now(UTC)
    return SettlementRequested(
        event_id=uuid4(),
        timestamp=now,
        tenant_id=promotion.tenant_id,
        promotion_id=promotion.id,
        promotion_internal_code=promotion.internal_code,
        blogger_id=promotion.blogger_id,
        style_id=promotion.style_id,
        amount=Decimal("300.00"),
        pr_id=promotion.pr_id,
        requested_by=requested_by,
        requested_at=now,
        line_id=line_id,
    )


class TestCreatedOnRequest:
    """F1：建单直接到待付款（原为待核查），写 settlement.created。"""

    @pytest.mark.parametrize("pass_line_id", [True, False])
    async def test_listener_creates_pending_payment_with_event(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        pass_line_id: bool,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            manager = await factory.user(tenant_a, roles=[pr_manager_role])
            promotion = await promotion_factory.promotion(
                style=await product_factory.style(),
                blogger=await blogger_factory.blogger(),
                pr=pr,
                publish_status="已发布",
                settlement_status="待付款",
            )
            promotion_id = promotion.id
            line_id = await _line_id(session, promotion_id)
            await on_settlement_requested(
                _request(
                    promotion,
                    requested_by=manager.id,
                    line_id=line_id if pass_line_id else None,
                ),
                session,
            )

            row = (
                await session.execute(
                    select(Settlement).where(Settlement.promotion_id == promotion_id)
                )
            ).scalar_one()
            assert row.settlement_status == "待付款"
            assert row.amount == row.total_amount == Decimal("300.00")
            events = await _events(session, promotion_id, LineEventType.SETTLEMENT_CREATED)
            assert len(events) == 1
            ev = events[0]
            assert ev.line_id == line_id
            assert (ev.from_state, ev.to_state) == (None, "待付款")
            assert ev.actor_id == manager.id
            assert ev.payload == {"settlement_id": str(row.id)}
            assert_payload_registered(ev.payload)
        finally:
            tenant_id_ctx.reset(token)


class TestReviewApprove:
    """F0：approve 只给旧的待核查单；新单不再经过核查。"""

    async def test_new_settlement_approve_is_illegal(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            manager = await factory.user(tenant_a, roles=[pr_manager_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            with pytest.raises(IllegalStateTransitionError) as exc_info:
                await SettlementService(session).review(
                    s.id, SettlementReviewRequest(action=ReviewAction.APPROVE), manager
                )
            assert exc_info.value.status_code == 422
            assert await _status(session, s.id) == "待付款"
            assert (
                await _events(session, s.promotion_id, LineEventType.SETTLEMENT_LEGACY_APPROVED)
                == []
            )
        finally:
            tenant_id_ctx.reset(token)

    async def test_legacy_pending_review_approves_to_pending_payment(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            manager = await factory.user(tenant_a, roles=[pr_manager_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待核查"
            )
            resp = await SettlementService(session).review(
                s.id, SettlementReviewRequest(action=ReviewAction.APPROVE), manager
            )
            assert resp.settlement_status == "待付款"
            events = await _events(
                session, s.promotion_id, LineEventType.SETTLEMENT_LEGACY_APPROVED
            )
            assert len(events) == 1
            assert (events[0].from_state, events[0].to_state) == ("待核查", "待付款")
            assert events[0].actor_id == manager.id
            assert events[0].payload == {"settlement_id": str(s.id)}
        finally:
            tenant_id_ctx.reset(token)


class TestExtraItem:
    """F2：类型「运费」停用（避免与 R33 重复），其他类型照常并写事件。"""

    async def test_shipping_item_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            manager = await factory.user(tenant_a, roles=[pr_manager_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            with pytest.raises(ExtraItemNotAllowedError) as exc_info:
                await SettlementService(session).add_extra_item(
                    s.id,
                    SettlementExtraItemCreateRequest(
                        item_type=ExtraItemType.SHIPPING, amount=Decimal("12.00")
                    ),
                    manager,
                )
            assert exc_info.value.status_code == 422
            assert exc_info.value.details["item_type"] == "运费"
            items = (
                await session.execute(
                    select(SettlementExtraItem).where(SettlementExtraItem.settlement_id == s.id)
                )
            ).all()
            assert items == []
        finally:
            tenant_id_ctx.reset(token)

    async def test_reward_item_recomputes_total_and_writes_event(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            manager = await factory.user(tenant_a, roles=[pr_manager_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            resp = await SettlementService(session).add_extra_item(
                s.id,
                SettlementExtraItemCreateRequest(
                    item_type=ExtraItemType.REWARD, amount=Decimal("50.00")
                ),
                manager,
            )
            assert resp.total_amount == Decimal("550.00")
            events = await _events(
                session, s.promotion_id, LineEventType.SETTLEMENT_EXTRA_ITEM_ADDED
            )
            assert len(events) == 1
            assert (events[0].from_state, events[0].to_state) == (None, None)
            assert events[0].payload == {
                "settlement_id": str(s.id),
                "item_type": "赞奖",
                "item_amount": "50.00",
                "total_amount": "550.00",
            }
            assert_payload_registered(events[0].payload)
        finally:
            tenant_id_ctx.reset(token)


class TestFillPayment:
    """F3：实付 ≥ 0；与应付不一致时调整说明必填。"""

    async def test_mismatch_without_note_is_gate_missing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            for note in (None, "   "):
                with pytest.raises(FlowGateMissingError) as exc_info:
                    await SettlementService(session).fill_payment_amount(
                        s.id,
                        SettlementPaymentAmountRequest(
                            payment_amount=Decimal("480.00"), payment_adjust_note=note
                        ),
                        finance,
                    )
                assert exc_info.value.details["missing"] == [
                    {"key": "payment_adjust_note", "label": "调整说明"}
                ]
            assert await _status(session, s.id) == "待付款"
            assert (
                await _events(session, s.promotion_id, LineEventType.SETTLEMENT_PAYMENT_FILLED)
                == []
            )
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize(
        ("amount", "note"),
        [
            (Decimal("480.00"), "扣 20 元：发布晚了一天"),
            (Decimal("0"), "全额扣除：内容不合格"),  # 0 = 全额扣除（11-63）
            (Decimal("500.00"), None),  # 与应付相等，说明可空
        ],
    )
    async def test_fill_moves_to_pending_finance_with_event(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        amount: Decimal,
        note: str | None,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            resp = await SettlementService(session).fill_payment_amount(
                s.id,
                SettlementPaymentAmountRequest(payment_amount=amount, payment_adjust_note=note),
                finance,
            )
            assert resp.settlement_status == "待财务付款"
            assert resp.payment_amount == amount
            stored = (
                await session.execute(
                    sa_text("SELECT payment_adjust_note FROM settlement WHERE id = :id"),
                    {"id": s.id},
                )
            ).scalar_one()
            assert stored == note
            events = await _events(session, s.promotion_id, LineEventType.SETTLEMENT_PAYMENT_FILLED)
            assert len(events) == 1
            assert (events[0].from_state, events[0].to_state) == ("待付款", "待财务付款")
            expected: dict[str, Any] = {
                "settlement_id": str(s.id),
                "payment_amount": f"{amount:.2f}",
            }
            if note is not None:
                expected["payment_adjust_note"] = note
            assert events[0].payload == expected
            assert_payload_registered(events[0].payload)
        finally:
            tenant_id_ctx.reset(token)

    async def test_schema_bounds(self) -> None:
        with pytest.raises(PydanticValidationError):
            SettlementPaymentAmountRequest(payment_amount=Decimal("-1"))
        with pytest.raises(PydanticValidationError):
            SettlementPaymentAmountRequest(
                payment_amount=Decimal("1"), payment_adjust_note="x" * 2001
            )
        assert (
            SettlementPaymentAmountRequest(payment_amount=Decimal("0")).payment_adjust_note is None
        )


class TestPaymentDate:
    """F4 / D5：付款日期 = 北京时间的当天，客户端传什么都忽略。"""

    @freeze_time(_FROZEN_UTC)
    async def test_json_entry_ignores_client_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        attachment_factory: Any,
        event_capture: list[Any],
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            assert date.today() == date(2026, 5, 26), "场景有效性：容器日期与北京日期不同"
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory,
                product_factory,
                blogger_factory,
                status="待财务付款",
                payment_amount=Decimal("480.00"),
            )
            att = await attachment_factory.attachment()
            resp = await SettlementService(session).upload_payment_proof(
                s.id,
                SettlementPaymentProofRequest(
                    payment_date=date(2026, 5, 25), payment_proof_attachment_id=att.id
                ),
                finance,
            )
            assert resp.settlement_status == "已付款"
            assert resp.payment_date == _BEIJING_TODAY
            paid = [e for e in event_capture if e.event_type == "SettlementPaid"]
            assert len(paid) == 1 and paid[0].payment_date == _BEIJING_TODAY
            events = await _events(session, s.promotion_id, LineEventType.SETTLEMENT_PAID)
            assert len(events) == 1
            ev = events[0]
            assert (ev.from_state, ev.to_state) == ("待财务付款", "已付款")
            assert ev.attachment_id == att.id
            assert ev.actor_id == finance.id
            assert ev.payload == {
                "settlement_id": str(s.id),
                "payment_amount": "480.00",
                "payment_date": "2026-05-27",
            }
            assert_payload_registered(ev.payload)
        finally:
            tenant_id_ctx.reset(token)

    @freeze_time(_FROZEN_UTC)
    async def test_upload_entry_uses_beijing_today(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        r2: _RecordingS3,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory,
                product_factory,
                blogger_factory,
                status="待财务付款",
                payment_amount=Decimal("480.00"),
            )
            resp = await SettlementService(session).upload_payment_proof_file(
                s.id, filename="proof.png", mime_type="image/png", data=_PNG, user=finance
            )
            assert resp.payment_date == _BEIJING_TODAY
            events = await _events(session, s.promotion_id, LineEventType.SETTLEMENT_PAID)
            assert len(events) == 1
            assert events[0].attachment_id == resp.payment_proof_attachment_id
        finally:
            tenant_id_ctx.reset(token)

    async def test_future_client_date_is_ignored_not_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        attachment_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory,
                product_factory,
                blogger_factory,
                status="待财务付款",
                payment_amount=Decimal("480.00"),
            )
            att = await attachment_factory.attachment()
            resp = await SettlementService(session).upload_payment_proof(
                s.id,
                SettlementPaymentProofRequest(
                    payment_date=get_today() + timedelta(days=3),
                    payment_proof_attachment_id=att.id,
                ),
                finance,
            )
            assert resp.payment_date == get_today()
            # 不传日期也行（旧 JSON 入口改可选）
            assert (
                SettlementPaymentProofRequest(payment_proof_attachment_id=att.id).payment_date
                is None
            )
        finally:
            tenant_id_ctx.reset(token)

    async def test_upload_form_payment_date_optional_in_openapi(self) -> None:
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            spec = (await ac.get("/api/openapi.json")).json()
        body = spec["paths"]["/api/settlements/{settlement_id}/payment-proof/upload"]["post"][
            "requestBody"
        ]["content"]["multipart/form-data"]["schema"]
        ref = body["$ref"].rsplit("/", 1)[-1]
        schema = spec["components"]["schemas"][ref]
        assert (
            "payment_date" in schema["properties"]
        ), "场景有效性：表单仍接受这个字段（兼容旧前端）"
        assert schema["required"] == ["file"]
