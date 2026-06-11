"""U05 集成测试：mark_paid 全流程（FB4 attachment 6 项 + FB5 反向事件 + 跨租户）。

合并：
- upload_payment_proof 成功 → 已付款 + 发 SettlementPaid（FB5）
- attachment 6 项校验失败拦截（FB4）
- 跨租户 attachment → InvalidAttachmentReferenceError（FB4）
- SettlementPaid 反向 listener 同步 promotion.settlement_status（FB5）
- 反向事件失败不阻塞主流程（FB5 不对称）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.core.tenancy import tenant_id_ctx
from app.modules.finance.enums import SettlementStatus
from app.modules.finance.exceptions import InvalidAttachmentReferenceError
from app.modules.finance.models import Settlement
from app.modules.finance.schemas import SettlementPaymentProofRequest
from app.modules.finance.service import SettlementService
from app.modules.promotion.urge_calculator import get_today


@pytest.mark.integration
@pytest.mark.asyncio
class TestMarkPaidHappyPath:
    async def test_upload_proof_marks_paid_and_emits_event(
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
        """FB4 + FB5：校验通过 → 已付款 + SettlementPaid 反向事件。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待财务付款",
                payment_amount=Decimal("480.00"),
            )
            att = await attachment_factory.attachment()  # 默认 6 项全合规
            svc = SettlementService(session)
            resp = await svc.upload_payment_proof(
                s.id,
                SettlementPaymentProofRequest(
                    payment_date=get_today(),
                    payment_proof_attachment_id=att.id,
                ),
                finance,
            )
            assert resp.settlement_status == "已付款"
            assert resp.payment_proof_attachment_id == att.id

            # FB5：发了 SettlementPaid
            paid_events = [
                e for e in event_capture if e.event_type == "SettlementPaid"
            ]
            assert len(paid_events) == 1
            assert paid_events[0].settlement_id == s.id
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestMarkPaidAttachmentValidation:
    async def test_cross_tenant_attachment_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
        attachment_factory: Any,
    ) -> None:
        """FB4：attachment 属于 tenant_b，tenant_a 财务引用 → 拒绝。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待财务付款",
                payment_amount=Decimal("480.00"),
            )
            # attachment 属于 tenant_b
            foreign_att = await attachment_factory.attachment(tenant=tenant_b)
            svc = SettlementService(session)
            with pytest.raises(InvalidAttachmentReferenceError):
                await svc.upload_payment_proof(
                    s.id,
                    SettlementPaymentProofRequest(
                        payment_date=get_today(),
                        payment_proof_attachment_id=foreign_att.id,
                    ),
                    finance,
                )
            # 状态未推进
            await session.refresh(s)
            assert s.settlement_status == "待财务付款"
        finally:
            tenant_id_ctx.reset(token)

    async def test_not_ready_attachment_rejected(
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
        """FB4：attachment status='uploading' → 拒绝。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待财务付款",
            )
            att = await attachment_factory.attachment(status="uploading")
            svc = SettlementService(session)
            with pytest.raises(Exception) as exc_info:
                await svc.upload_payment_proof(
                    s.id,
                    SettlementPaymentProofRequest(
                        payment_date=get_today(),
                        payment_proof_attachment_id=att.id,
                    ),
                    finance,
                )
            assert "ATTACHMENT" in str(
                getattr(exc_info.value, "code", "")
            ) or exc_info.type.__name__ == "AttachmentNotReadyError"
        finally:
            tenant_id_ctx.reset(token)

    async def test_missing_attachment_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """FB4：attachment_id 不存在 → 拒绝。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待财务付款",
            )
            svc = SettlementService(session)
            with pytest.raises(InvalidAttachmentReferenceError):
                await svc.upload_payment_proof(
                    s.id,
                    SettlementPaymentProofRequest(
                        payment_date=get_today(),
                        payment_proof_attachment_id=uuid4(),
                    ),
                    finance,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestSettlementPaidReverseListener:
    """FB5：U05 → U04 反向 listener 同步 + 缺失容忍。"""

    async def test_paid_event_syncs_promotion(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        attachment_factory: Any,
        cross_unit_event_bus: Any,
    ) -> None:
        """mark_paid → SettlementPaid → promotion.settlement_status='已付款'。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger,
                publish_status="已发布", settlement_status="待付款",
            )
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, promotion=promotion,
                settlement_status="待财务付款", payment_amount=Decimal("480.00"),
            )
            att = await attachment_factory.attachment()
            svc = SettlementService(session)
            await svc.upload_payment_proof(
                s.id,
                SettlementPaymentProofRequest(
                    payment_date=get_today(),
                    payment_proof_attachment_id=att.id,
                ),
                finance,
            )
            # 反向同步：promotion.settlement_status → 已付款
            await session.refresh(promotion)
            assert promotion.settlement_status == "已付款"
        finally:
            tenant_id_ctx.reset(token)

    async def test_paid_event_no_listener_does_not_block(
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
        """FB5：无 SettlementPaid listener（autouse clear_handlers）→ mark_paid 仍成功。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待财务付款", payment_amount=Decimal("480.00"),
            )
            att = await attachment_factory.attachment()
            svc = SettlementService(session)
            resp = await svc.upload_payment_proof(
                s.id,
                SettlementPaymentProofRequest(
                    payment_date=get_today(),
                    payment_proof_attachment_id=att.id,
                ),
                finance,
            )
            # 主流程成功（通知类事件无 handler 不抛错，required_handler=False）
            assert resp.settlement_status == "已付款"
        finally:
            tenant_id_ctx.reset(token)

    async def test_paid_event_listener_failure_does_not_block(
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
        """FB5 不对称：SettlementPaid handler 抛错 → mark_paid 主流程仍成功。"""

        async def failing_handler(_event: Any, _session: Any) -> None:
            raise RuntimeError("reverse sync boom")

        subscribe("SettlementPaid", failing_handler)

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待财务付款", payment_amount=Decimal("480.00"),
            )
            att = await attachment_factory.attachment()
            svc = SettlementService(session)
            resp = await svc.upload_payment_proof(
                s.id,
                SettlementPaymentProofRequest(
                    payment_date=get_today(),
                    payment_proof_attachment_id=att.id,
                ),
                finance,
            )
            # 不对称：通知类失败不重新 raise，主流程已付款成功
            assert resp.settlement_status == "已付款"
            row = (
                await session.execute(
                    select(Settlement).where(Settlement.id == s.id)
                )
            ).scalar_one()
            assert row.settlement_status == SettlementStatus.PAID.value
        finally:
            tenant_id_ctx.reset(token)
