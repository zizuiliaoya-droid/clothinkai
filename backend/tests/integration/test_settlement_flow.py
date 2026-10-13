"""流程线 PR-6：结款单 F0 ~ F4（设计 3.4、7.5、9.3 D「结款单」行）。

- F1：``SettlementRequested`` 建单直接落在「待付款」，同事务写 ``settlement.created``
- F0：只给上线前停在「待核查」的旧单走完；新单（待付款）approve → 422
- F2：增加结算项「运费」过渡期保留（设计 10.3 PR-6「过渡期」，PR-8 与 R33 同时停用）
- F3：实付 ≥ 0；实付 ≠ 应付（``total_amount``）时调整说明必填（422 ``FLOW_GATE_MISSING``）
- F4：付款日期一律取北京时间的当天，客户端传的日期忽略（D5：容器是 UTC）
- 每一步各写一条结款组事件（6.3），付款事件的附件 = 付款凭证
- F5 → R6 / R7 → F6：``SettlementRejected`` / ``SettlementResubmitted`` 必须有监听，两张表同事务（D7、D12）
- R7 结款重提（``POST /promotions/{id}/settlement/resubmit``）：推广单与结款单一起回到待付款、实付与说明清空（D6）；
  没码 422；结款单不在已驳回 → 409、推广单不变
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

from app.core.events import dispatch, subscribe
from app.core.exceptions import (
    AppException,
    IllegalStateTransitionError,
    MissingRequiredHandlerError,
)
from app.core.tenancy import tenant_id_ctx
from app.modules.finance.enums import ExtraItemType
from app.modules.finance.exceptions import (
    SettlementTotalChangedError,
)
from app.modules.finance.exceptions import (
    StateTransitionConflictError as SettlementStateConflictError,
)
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
from app.modules.promotion.events import SettlementRequested, SettlementResubmitted
from app.modules.promotion.exceptions import (
    StateTransitionConflictError as PromotionStateConflictError,
)
from app.modules.promotion.schemas import PromotionSettlementResubmitRequest
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from tests.flow_event_helpers import assert_payload_registered
from tests.integration.test_settlement_proof_upload import _PNG, _RecordingS3
from tests.integration.test_settlement_routing import _call

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
    """F2：增加结算项照常并写事件；「运费」过渡期保留（设计 10.3 PR-6，PR-8 与 R33 同时停用）。"""

    async def test_shipping_item_allowed_in_transition(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """召回仍走旧流程，寄回运费在 PR-8 之前只能走结款单追加项：可加、金额进合计、写事件。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            manager = await factory.user(tenant_a, roles=[pr_manager_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            resp = await SettlementService(session).add_extra_item(
                s.id,
                SettlementExtraItemCreateRequest(
                    item_type=ExtraItemType.SHIPPING, amount=Decimal("12.00")
                ),
                manager,
            )
            assert resp.total_amount == Decimal("512.00")
            assert [(it.item_type, it.amount) for it in resp.extra_items] == [
                ("运费", Decimal("12.00"))
            ]
            items = (
                (
                    await session.execute(
                        select(SettlementExtraItem).where(SettlementExtraItem.settlement_id == s.id)
                    )
                )
                .scalars()
                .all()
            )
            assert [(it.item_type, it.amount) for it in items] == [("运费", Decimal("12.00"))]
            events = await _events(
                session, s.promotion_id, LineEventType.SETTLEMENT_EXTRA_ITEM_ADDED
            )
            assert len(events) == 1
            assert events[0].payload == {
                "settlement_id": str(s.id),
                "item_type": "运费",
                "item_amount": "12.00",
                "total_amount": "512.00",
            }
            assert_payload_registered(events[0].payload)
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

    @staticmethod
    def _hook_after_read(
        monkeypatch: pytest.MonkeyPatch, svc: SettlementService, side_effect: Any
    ) -> None:
        """在 ``fill_payment_amount`` 读出结款单之后、条件 UPDATE 之前插一次别人的操作（只插一次）。"""
        original = svc._repo.get_by_id
        fired = False

        async def read_then(settlement_id: UUID) -> Settlement | None:
            nonlocal fired
            row = await original(settlement_id)
            if not fired:
                fired = True
                await side_effect(settlement_id)
            return row

        monkeypatch.setattr(svc._repo, "get_by_id", read_then)

    @pytest.mark.parametrize(
        ("amount", "note"),
        [
            (Decimal("500.00"), None),  # 按读到的应付 500 判「一致、说明可空」，实际应付已是 520
            (Decimal("480.00"), "扣 20 元：发布晚了一天"),  # 说明是对着旧应付写的，同样作废
        ],
    )
    async def test_total_changed_between_read_and_update_is_conflict(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
        amount: Decimal,
        note: str | None,
    ) -> None:
        """L2：读完应付之后主管「增加结算项」→ 409 SETTLEMENT_TOTAL_CHANGED，实付和说明都不落。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            manager = await factory.user(tenant_a, roles=[pr_manager_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            settlement_id, promotion_id = s.id, s.promotion_id
            assert s.total_amount == Decimal("500.00")

            async def add_reward(sid: UUID) -> None:
                # 另一个请求的 add_extra_item 落库效果：追加 20 元赞奖、应付重算为 520。
                # 用原生 SQL 改应付，不经身份映射：fill 手里的对象仍是读到的 500。
                session.add(
                    SettlementExtraItem(
                        id=uuid4(),
                        tenant_id=tenant_a.id,
                        settlement_id=sid,
                        item_type=ExtraItemType.REWARD.value,
                        amount=Decimal("20.00"),
                        created_by=manager.id,
                    )
                )
                await session.flush()
                await session.execute(
                    sa_text("UPDATE settlement SET total_amount = 520.00 WHERE id = :id"),
                    {"id": sid},
                )

            svc = SettlementService(session)
            self._hook_after_read(monkeypatch, svc, add_reward)
            with pytest.raises(SettlementTotalChangedError) as exc_info:
                await svc.fill_payment_amount(
                    settlement_id,
                    SettlementPaymentAmountRequest(payment_amount=amount, payment_adjust_note=note),
                    finance,
                )
            assert exc_info.value.status_code == 409
            assert exc_info.value.code == "SETTLEMENT_TOTAL_CHANGED"
            assert exc_info.value.message == "应付金额刚有变动，请刷新后重新填写实付"
            row = (
                await session.execute(
                    sa_text(
                        "SELECT settlement_status, total_amount, payment_amount, "
                        "payment_adjust_note FROM settlement WHERE id = :id"
                    ),
                    {"id": settlement_id},
                )
            ).one()
            # 场景有效：应付确实被改成了 520
            assert tuple(row) == ("待付款", Decimal("520.00"), None, None)
            assert (
                await _events(session, promotion_id, LineEventType.SETTLEMENT_PAYMENT_FILLED) == []
            )
        finally:
            tenant_id_ctx.reset(token)

    async def test_status_changed_between_read_and_update_keeps_state_conflict(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """读完之后状态已不是待付款（别人先填了）：照旧 409 SETTLEMENT_STATE_CONFLICT，不报应付变动。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            settlement_id = s.id

            async def other_filled(sid: UUID) -> None:
                await session.execute(
                    sa_text(
                        "UPDATE settlement SET settlement_status = '待财务付款', "
                        "payment_amount = 500.00 WHERE id = :id"
                    ),
                    {"id": sid},
                )

            svc = SettlementService(session)
            self._hook_after_read(monkeypatch, svc, other_filled)
            with pytest.raises(SettlementStateConflictError) as exc_info:
                await svc.fill_payment_amount(
                    settlement_id,
                    SettlementPaymentAmountRequest(payment_amount=Decimal("500.00")),
                    finance,
                )
            assert not isinstance(exc_info.value, SettlementTotalChangedError)
            assert exc_info.value.status_code == 409
            assert exc_info.value.code == "SETTLEMENT_STATE_CONFLICT"
            assert await _status(session, settlement_id) == "待财务付款"
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


async def _promotion_status(session: AsyncSession, promotion_id: UUID) -> str:
    row = await session.execute(
        sa_text("SELECT settlement_status FROM promotion WHERE id = :id"), {"id": promotion_id}
    )
    return str(row.scalar_one())


async def _settlement_row(session: AsyncSession, settlement_id: UUID) -> tuple[Any, ...]:
    row = await session.execute(
        sa_text(
            "SELECT settlement_status, payment_amount, payment_adjust_note "
            "FROM settlement WHERE id = :id"
        ),
        {"id": settlement_id},
    )
    return tuple(row.one())


def _resubmitted(s: Settlement, *, actor_id: UUID) -> SettlementResubmitted:
    return SettlementResubmitted(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        tenant_id=s.tenant_id,
        promotion_id=s.promotion_id,
        settlement_id=s.id,
        note="收款码换好了",
        actor_id=actor_id,
    )


_REJECT = SettlementReviewRequest(action=ReviewAction.REJECT, review_reason="收款码不对")


class TestSettlementRejected:
    """F5 → R6：财务驳回与推广单「结款驳回」同一事务（D7、D12）。"""

    @pytest.mark.parametrize("from_status", ["待付款", "待财务付款"])
    async def test_reject_moves_promotion_and_writes_event(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        cross_unit_event_bus: None,
        from_status: str,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory,
                product_factory,
                blogger_factory,
                status=from_status,
                payment_amount=Decimal("480.00") if from_status == "待财务付款" else None,
            )
            assert await _promotion_status(session, s.promotion_id) == "待付款"
            resp = await SettlementService(session).review(s.id, _REJECT, finance)
            assert resp.settlement_status == "已驳回"
            assert await _promotion_status(session, s.promotion_id) == "结款驳回"
            events = await _events(session, s.promotion_id, LineEventType.SETTLEMENT_REJECTED)
            assert len(events) == 1
            ev = events[0]
            assert ev.line_id == await _line_id(session, s.promotion_id)
            assert (ev.from_state, ev.to_state) == (from_status, "已驳回")
            assert ev.note == "收款码不对"
            assert ev.actor_id == finance.id
            assert ev.payload == {"settlement_id": str(s.id)}
            assert_payload_registered(ev.payload)
        finally:
            tenant_id_ctx.reset(token)

    async def test_listener_failure_rolls_back_both_tables(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        cross_unit_event_bus: None,
    ) -> None:
        """监听里中途抛错 → 结款单、推广单、事件都不变（review 不能先 commit 再 dispatch）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            sid, pid = s.id, s.promotion_id
            await session.commit()
            seen: list[str] = []

            async def _boom(event: Any, sess: AsyncSession) -> None:
                seen.append(await _promotion_status(sess, event.promotion_id))
                raise RuntimeError("listener boom")

            subscribe("SettlementRejected", _boom)  # 排在真实监听之后
            with pytest.raises(RuntimeError, match="listener boom"):
                await SettlementService(session).review(sid, _REJECT, finance)
            assert seen == ["结款驳回"], "场景有效性：真实监听已在同一事务里改了推广单"
            await session.rollback()
            assert await _status(session, sid) == "待付款"
            assert await _promotion_status(session, pid) == "待付款"
            assert await _events(session, pid, LineEventType.SETTLEMENT_REJECTED) == []
        finally:
            tenant_id_ctx.reset(token)

    async def test_promotion_not_pending_payment_conflicts_both_unchanged(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        cross_unit_event_bus: None,
    ) -> None:
        """直接造一张结款单、推广单停在待核查 → 409 PROMOTION_STATE_CONFLICT，两边都不变。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                publish_status="已发布",
                settlement_status="待核查",
            )
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, promotion=promotion, settlement_status="待付款"
            )
            sid, pid = s.id, promotion.id
            await session.commit()
            with pytest.raises(PromotionStateConflictError) as exc_info:
                await SettlementService(session).review(sid, _REJECT, finance)
            assert exc_info.value.status_code == 409
            assert exc_info.value.code == "PROMOTION_STATE_CONFLICT"
            assert exc_info.value.details == {
                "promotion_id": str(pid),
                "settlement_id": str(sid),
            }
            await session.rollback()
            assert await _status(session, sid) == "待付款"
            assert await _promotion_status(session, pid) == "待核查"
            assert await _events(session, pid, LineEventType.SETTLEMENT_REJECTED) == []
        finally:
            tenant_id_ctx.reset(token)

    async def test_inactive_promotion_reject_conflicts_with_own_code(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        cross_unit_event_bus: None,
    ) -> None:
        """收尾 L3 兜底：推广单已停用（停用门之前的历史数据）+ 结款单待财务付款 → 409 ``PROMOTION_INACTIVE``，
        不再报「推广单不在待财务付款」；两边都不变。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                publish_status="已发布",
                settlement_status="待付款",
                is_active=False,
            )
            s = await settlement_factory.settlement(
                style=style,
                blogger=blogger,
                promotion=promotion,
                settlement_status="待财务付款",
                payment_amount=Decimal("500.00"),
            )
            sid, pid = s.id, promotion.id
            await session.commit()
            with pytest.raises(AppException) as exc_info:
                await SettlementService(session).review(sid, _REJECT, finance)
            assert exc_info.value.status_code == 409
            assert exc_info.value.code == "PROMOTION_INACTIVE"
            assert exc_info.value.message == "推广单已停用，不能驳回结款单"
            assert exc_info.value.details == {
                "promotion_id": str(pid),
                "settlement_id": str(sid),
            }
            await session.rollback()
            assert await _status(session, sid) == "待财务付款"
            assert await _promotion_status(session, pid) == "待付款"
            assert await _events(session, pid, LineEventType.SETTLEMENT_REJECTED) == []
        finally:
            tenant_id_ctx.reset(token)

    async def test_missing_listener_raises_and_settlement_unchanged(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """没注册 promotion 监听 → MissingRequiredHandlerError（不能按通知类静默丢掉）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="待付款"
            )
            sid, pid = s.id, s.promotion_id
            await session.commit()
            with pytest.raises(MissingRequiredHandlerError):
                await SettlementService(session).review(sid, _REJECT, finance)
            await session.rollback()
            assert await _status(session, sid) == "待付款"
            assert await _promotion_status(session, pid) == "待付款"
        finally:
            tenant_id_ctx.reset(token)


class TestSettlementResubmitted:
    """R7 → F6：finance 监听把结款单 已驳回 → 待付款、清空实付与说明，同事务、不 commit。"""

    async def test_listener_resets_settlement(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        cross_unit_event_bus: None,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            s = await _settlement(
                settlement_factory,
                product_factory,
                blogger_factory,
                status="已驳回",
                payment_amount=Decimal("480.00"),
                payment_adjust_note="扣 20 元",
            )
            assert await _settlement_row(session, s.id) == (
                "已驳回",
                Decimal("480.00"),
                "扣 20 元",
            ), "场景有效性：重提前实付与说明都有值"
            await dispatch(_resubmitted(s, actor_id=pr.id), session=session)
            assert await _settlement_row(session, s.id) == ("待付款", None, None)
        finally:
            tenant_id_ctx.reset(token)

    async def test_listener_does_not_commit(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        cross_unit_event_bus: None,
    ) -> None:
        """之后的处理抛错 → 结款单回到已驳回、实付仍在（监听用 autocommit=False）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            s = await _settlement(
                settlement_factory,
                product_factory,
                blogger_factory,
                status="已驳回",
                payment_amount=Decimal("480.00"),
            )
            event = _resubmitted(s, actor_id=pr.id)
            await session.commit()
            seen: list[tuple[Any, ...]] = []

            async def _boom(ev: Any, sess: AsyncSession) -> None:
                seen.append(await _settlement_row(sess, ev.settlement_id))
                raise RuntimeError("listener boom")

            subscribe("SettlementResubmitted", _boom)
            with pytest.raises(RuntimeError, match="listener boom"):
                await dispatch(event, session=session)
            assert seen == [("待付款", None, None)], "场景有效性：真实监听已改了结款单"
            await session.rollback()
            assert await _settlement_row(session, event.settlement_id) == (
                "已驳回",
                Decimal("480.00"),
                None,
            )
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize("status", ["待付款", "待财务付款", "已付款"])
    async def test_not_rejected_conflicts(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        cross_unit_event_bus: None,
        status: str,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status=status
            )
            with pytest.raises(SettlementStateConflictError) as exc_info:
                await dispatch(_resubmitted(s, actor_id=pr.id), session=session)
            assert exc_info.value.status_code == 409
            assert exc_info.value.details["settlement_status"] == status
            assert (await _settlement_row(session, s.id))[0] == status
        finally:
            tenant_id_ctx.reset(token)

    async def test_missing_listener_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            s = await _settlement(
                settlement_factory, product_factory, blogger_factory, status="已驳回"
            )
            with pytest.raises(MissingRequiredHandlerError):
                await dispatch(_resubmitted(s, actor_id=pr.id), session=session)
            assert (await _settlement_row(session, s.id))[0] == "已驳回"
        finally:
            tenant_id_ctx.reset(token)


_RESUBMIT = PromotionSettlementResubmitRequest(note="收款码换好了，请重新付款")


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


async def _rejected_pair(
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    settlement_factory: Any,
    *,
    payment_qr: bool = True,
    promotion_status: str = "结款驳回",
    settlement_status: str = "已驳回",
) -> tuple[UUID, UUID, UUID]:
    """财务驳回后的一对：推广单结款驳回（有码）、结款单已驳回且填过实付与说明。返回 (推广单, 结款单, 线)。"""
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    promo = await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        pr=flow_users.pr,
        publish_status="已发布",
        settlement_status=promotion_status,
        quote_amount=Decimal("500.00"),
        payment_qr=payment_qr,
        actual_publish_date=get_today() - timedelta(days=10),
    )
    s = await settlement_factory.settlement(
        style=style,
        blogger=blogger,
        promotion=promo,
        settlement_status=settlement_status,
        payment_amount=Decimal("480.00"),
        payment_adjust_note="扣 20 元",
    )
    return promo.id, s.id, promo.line_id


@pytest.mark.usefixtures("tenant_ctx")
class TestSettlementResubmitR7:
    """R7 → F6：推广单 结款驳回 → 待付款，结款单 已驳回 → 待付款，同一事务（D6、D7、D12）。"""

    @pytest.mark.usefixtures("cross_unit_event_bus")
    @pytest.mark.parametrize("who", ["pr", "pr_manager", "admin"])
    async def test_resubmit_moves_both_and_clears_payment(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        who: str,
    ) -> None:
        pid, sid, line_id = await _rejected_pair(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        assert await _settlement_row(session, sid) == (
            "已驳回",
            Decimal("480.00"),
            "扣 20 元",
        ), "场景有效性：重提前实付与说明都有值"
        user = getattr(flow_users, who)
        resp = await PromotionService(session).resubmit_settlement(pid, _RESUBMIT, user)
        assert resp.settlement_status == "待付款"
        assert await _promotion_status(session, pid) == "待付款"
        assert await _settlement_row(session, sid) == ("待付款", None, None)
        events = await _events(session, pid, LineEventType.SETTLEMENT_RESUBMITTED)
        assert len(events) == 1
        ev = events[0]
        assert ev.line_id == line_id
        assert (ev.from_state, ev.to_state) == ("已驳回", "待付款")
        assert ev.note == "收款码换好了，请重新付款"
        assert ev.actor_id == user.id
        assert ev.payload == {"settlement_id": str(sid)}
        assert_payload_registered(ev.payload)

    @pytest.mark.usefixtures("cross_unit_event_bus")
    async def test_no_payment_qr_422_both_unchanged(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        pid, sid, _ = await _rejected_pair(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            payment_qr=False,
        )
        with pytest.raises(FlowGateMissingError) as exc_info:
            await PromotionService(session).resubmit_settlement(pid, _RESUBMIT, flow_users.pr)
        assert exc_info.value.status_code == 422
        assert [m["key"] for m in exc_info.value.details["missing"]] == ["payment_qr"]
        assert await _promotion_status(session, pid) == "结款驳回"
        assert await _settlement_row(session, sid) == ("已驳回", Decimal("480.00"), "扣 20 元")
        assert await _events(session, pid, LineEventType.SETTLEMENT_RESUBMITTED) == []

    @pytest.mark.usefixtures("cross_unit_event_bus")
    @pytest.mark.parametrize("settlement_status", ["待付款", "待财务付款", "已付款"])
    async def test_settlement_not_rejected_conflicts_promotion_unchanged(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        settlement_status: str,
    ) -> None:
        """推广单在结款驳回、结款单却不在已驳回 → finance 监听 409，推广单跟着回滚。"""
        pid, sid, _ = await _rejected_pair(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            settlement_status=settlement_status,
        )
        await session.commit()
        with pytest.raises(SettlementStateConflictError) as exc_info:
            await PromotionService(session).resubmit_settlement(pid, _RESUBMIT, flow_users.pr)
        assert exc_info.value.status_code == 409
        assert exc_info.value.details["settlement_status"] == settlement_status
        await session.rollback()
        assert await _promotion_status(session, pid) == "结款驳回"
        assert await _settlement_row(session, sid) == (
            settlement_status,
            Decimal("480.00"),
            "扣 20 元",
        )
        assert await _events(session, pid, LineEventType.SETTLEMENT_RESUBMITTED) == []

    @pytest.mark.usefixtures("cross_unit_event_bus")
    async def test_listener_failure_rolls_back_both(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """监听里中途抛错 → 推广单、结款单、事件都不变（推广 service 不能先 commit 再 dispatch）。"""
        pid, sid, _ = await _rejected_pair(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        await session.commit()
        seen: list[tuple[str, tuple[Any, ...]]] = []

        async def _boom(ev: Any, sess: AsyncSession) -> None:
            seen.append(
                (
                    await _promotion_status(sess, ev.promotion_id),
                    await _settlement_row(sess, ev.settlement_id),
                )
            )
            raise RuntimeError("listener boom")

        subscribe("SettlementResubmitted", _boom)  # 排在真实监听之后
        with pytest.raises(RuntimeError, match="listener boom"):
            await PromotionService(session).resubmit_settlement(pid, _RESUBMIT, flow_users.pr)
        assert seen == [
            ("待付款", ("待付款", None, None))
        ], "场景有效性：推广单与真实监听都已在同一事务里改过"
        await session.rollback()
        assert await _promotion_status(session, pid) == "结款驳回"
        assert await _settlement_row(session, sid) == ("已驳回", Decimal("480.00"), "扣 20 元")
        assert await _events(session, pid, LineEventType.SETTLEMENT_RESUBMITTED) == []

    async def test_missing_listener_raises_promotion_unchanged(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        pid, sid, _ = await _rejected_pair(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        await session.commit()
        with pytest.raises(MissingRequiredHandlerError):
            await PromotionService(session).resubmit_settlement(pid, _RESUBMIT, flow_users.pr)
        await session.rollback()
        assert await _promotion_status(session, pid) == "结款驳回"
        assert (await _settlement_row(session, sid))[0] == "已驳回"

    @pytest.mark.usefixtures("cross_unit_event_bus")
    @pytest.mark.parametrize("promotion_status", ["待付款", "已付款", "已驳回"])
    async def test_promotion_not_settlement_rejected_422(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        promotion_status: str,
    ) -> None:
        pid, sid, _ = await _rejected_pair(
            flow_users,
            product_factory,
            blogger_factory,
            promotion_factory,
            settlement_factory,
            promotion_status=promotion_status,
        )
        with pytest.raises(AppException) as exc_info:
            await PromotionService(session).resubmit_settlement(pid, _RESUBMIT, flow_users.pr)
        assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"
        assert exc_info.value.status_code == 422
        assert await _promotion_status(session, pid) == promotion_status
        assert (await _settlement_row(session, sid))[0] == "已驳回"


@pytest.mark.usefixtures("tenant_ctx", "cross_unit_event_bus")
class TestSettlementResubmitApi:
    """路由：``promotion:write``；说明必填（全空白 = 没填）。"""

    async def test_pr_resubmits_via_route(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
    ) -> None:
        pid, sid, _ = await _rejected_pair(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        resp = await _call(
            session,
            flow_users.pr,
            "POST",
            f"/api/promotions/{pid}/settlement/resubmit",
            json={"note": "收款码换好了"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["settlement_status"] == "待付款"
        assert await _settlement_row(session, sid) == ("待付款", None, None)

    @pytest.mark.parametrize("note", ["   ", "", "x" * 2001], ids=["空白", "空", "超长"])
    async def test_note_required(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        note: str,
    ) -> None:
        pid, sid, _ = await _rejected_pair(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        resp = await _call(
            session,
            flow_users.pr,
            "POST",
            f"/api/promotions/{pid}/settlement/resubmit",
            json={"note": note},
        )
        assert resp.status_code == 422, resp.text
        assert await _promotion_status(session, pid) == "结款驳回"
        assert (await _settlement_row(session, sid))[0] == "已驳回"

    @pytest.mark.parametrize("who", ["finance", "operations", "warehouse"])
    async def test_roles_without_promotion_write_403(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        who: str,
    ) -> None:
        pid, sid, _ = await _rejected_pair(
            flow_users, product_factory, blogger_factory, promotion_factory, settlement_factory
        )
        resp = await _call(
            session,
            getattr(flow_users, who),
            "POST",
            f"/api/promotions/{pid}/settlement/resubmit",
            json={"note": "收款码换好了"},
        )
        assert resp.status_code == 403, resp.text
        assert await _promotion_status(session, pid) == "结款驳回"
        assert (await _settlement_row(session, sid))[0] == "已驳回"
