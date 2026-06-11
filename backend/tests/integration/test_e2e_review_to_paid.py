"""U05 端到端集成测试（J4 完整旅程，FB1 强一致 + 双向事件）。

U04 review approve → SettlementRequested → U05 settlement 创建（待核查）
→ PR 主管 review approve → fill_payment → 财务 mark_paid
→ SettlementPaid → promotion.settlement_status='已付款'
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.finance.models import Settlement
from app.modules.finance.schemas import (
    SettlementPaymentAmountRequest,
    SettlementPaymentProofRequest,
    SettlementReviewRequest,
)
from app.modules.finance.service import SettlementService
from app.modules.promotion.enums import ReviewAction
from app.modules.promotion.schemas import PromotionReviewRequest
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today


@pytest.mark.integration
@pytest.mark.asyncio
class TestE2EReviewToPaid:
    async def test_full_journey_j4(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        pr_role: Any,
        pr_manager_role: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        attachment_factory: Any,
        cross_unit_event_bus: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            pr_manager = await factory.user(tenant_a, roles=[pr_manager_role])
            finance = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=pr,
                publish_status="已发布", settlement_status="待核查",
                quote_amount=Decimal("500.00"),
            )

            # 1. U04 review approve → SettlementRequested → U05 创建 settlement
            promo_svc = PromotionService(session)
            await promo_svc.review(
                promotion.id,
                PromotionReviewRequest(action=ReviewAction.APPROVE),
                pr_manager,
            )
            await session.flush()

            settlement = (
                await session.execute(
                    select(Settlement).where(
                        Settlement.promotion_id == promotion.id
                    )
                )
            ).scalar_one()
            assert settlement.settlement_status == "待核查"  # FB1 起点

            # 2. U05 PR 主管 review approve → 待付款
            settle_svc = SettlementService(session)
            await settle_svc.review(
                settlement.id,
                SettlementReviewRequest(action=ReviewAction.APPROVE),
                pr_manager,
            )

            # 3. fill_payment → 待财务付款
            await settle_svc.fill_payment_amount(
                settlement.id,
                SettlementPaymentAmountRequest(payment_amount=Decimal("480.00")),
                pr_manager,
            )

            # 4. 财务 mark_paid → 已付款 + SettlementPaid 反向同步
            att = await attachment_factory.attachment()
            resp = await settle_svc.upload_payment_proof(
                settlement.id,
                SettlementPaymentProofRequest(
                    payment_date=get_today(),
                    payment_proof_attachment_id=att.id,
                ),
                finance,
            )
            assert resp.settlement_status == "已付款"

            # 5. 反向同步验证：promotion.settlement_status='已付款'
            await session.refresh(promotion)
            assert promotion.settlement_status == "已付款"
        finally:
            tenant_id_ctx.reset(token)
