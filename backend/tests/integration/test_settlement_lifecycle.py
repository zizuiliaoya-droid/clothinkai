"""U05 集成测试：settlement 状态推进生命周期（合并 review/extra_item/fill_payment/resubmit/immutable）。

按 U04 经验按内聚合并：
- review approve/reject + 自审禁止（EP06-S03/S04）
- add_extra_item + total 重算 + 状态约束（EP06-S05）
- fill_payment_amount（EP06-S06）
- resubmit（已驳回 → 待核查）
- FB3 immutable：DELETE 不提供（service 无软删接口）
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.finance.enums import ExtraItemType, SettlementStatus
from app.modules.finance.exceptions import (
    ExtraItemNotAllowedError,
    FieldPermissionDenied,
    SelfReviewForbiddenError,
)
from app.modules.finance.models import Settlement
from app.modules.finance.schemas import (
    SettlementExtraItemCreateRequest,
    SettlementPaymentAmountRequest,
    SettlementReviewRequest,
)
from app.modules.finance.service import SettlementService
from app.modules.promotion.enums import ReviewAction


@pytest.mark.integration
@pytest.mark.asyncio
class TestReview:
    async def test_approve_to_pending_payment(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            reviewer = await factory.user(tenant_a, roles=[pr_manager_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, pr=pr,
                settlement_status="待核查",
            )
            svc = SettlementService(session)
            resp = await svc.review(
                s.id, SettlementReviewRequest(action=ReviewAction.APPROVE), reviewer
            )
            assert resp.settlement_status == "待付款"
            assert resp.reviewed_by == reviewer.id
        finally:
            tenant_id_ctx.reset(token)

    async def test_reject_requires_reason_and_sets_status(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            reviewer = await factory.user(tenant_a, roles=[pr_manager_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, pr=pr,
                settlement_status="待核查",
            )
            svc = SettlementService(session)
            resp = await svc.review(
                s.id,
                SettlementReviewRequest(action=ReviewAction.REJECT, review_reason="金额有误"),
                reviewer,
            )
            assert resp.settlement_status == "已驳回"
            assert resp.review_reason == "金额有误"
        finally:
            tenant_id_ctx.reset(token)

    async def test_self_review_forbidden(
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
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, pr=user,
                settlement_status="待核查",
            )
            svc = SettlementService(session)
            with pytest.raises(SelfReviewForbiddenError):
                await svc.review(
                    s.id, SettlementReviewRequest(action=ReviewAction.APPROVE), user
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestExtraItem:
    async def test_add_extra_item_recomputes_total(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """EP06-S05：增加运费 → total_amount = amount + extra。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待付款",
                amount=Decimal("500.00"), total_amount=Decimal("500.00"),
            )
            svc = SettlementService(session)
            resp = await svc.add_extra_item(
                s.id,
                SettlementExtraItemCreateRequest(
                    item_type=ExtraItemType.SHIPPING, amount=Decimal("30.00")
                ),
                user,
            )
            assert resp.total_amount == Decimal("530.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_extra_item_rejected_when_not_pending_payment(
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
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待核查",  # 非待付款
            )
            svc = SettlementService(session)
            with pytest.raises(ExtraItemNotAllowedError):
                await svc.add_extra_item(
                    s.id,
                    SettlementExtraItemCreateRequest(
                        item_type=ExtraItemType.REWARD, amount=Decimal("10.00")
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_extra_item_field_permission_denied_for_pr(
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
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, settlement_status="待付款",
            )
            svc = SettlementService(session)
            with pytest.raises(FieldPermissionDenied):
                await svc.add_extra_item(
                    s.id,
                    SettlementExtraItemCreateRequest(
                        item_type=ExtraItemType.OTHER, amount=Decimal("5.00")
                    ),
                    pr,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestFillPaymentAndResubmit:
    async def test_fill_payment_amount(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_manager_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """EP06-S06：待付款 → 待财务付款。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, settlement_status="待付款",
            )
            svc = SettlementService(session)
            resp = await svc.fill_payment_amount(
                s.id,
                SettlementPaymentAmountRequest(payment_amount=Decimal("480.00")),
                user,
            )
            assert resp.settlement_status == "待财务付款"
            assert resp.payment_amount == Decimal("480.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_fill_payment_denied_for_finance(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        settlement_factory: Any,
    ) -> None:
        """财务可见金额但不可写 payment_amount。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[finance_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, settlement_status="待付款",
            )
            svc = SettlementService(session)
            with pytest.raises(FieldPermissionDenied):
                await svc.fill_payment_amount(
                    s.id,
                    SettlementPaymentAmountRequest(
                        payment_amount=Decimal("480.00")
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_resubmit_from_rejected(
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
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            s = await settlement_factory.settlement(
                style=style, blogger=blogger, settlement_status="已驳回",
            )
            svc = SettlementService(session)
            resp = await svc.resubmit(s.id, user)
            assert resp.settlement_status == "待核查"
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestImmutable:
    """FB3：财务记录永久不可替换。"""

    async def test_settlement_has_no_is_active_field(self) -> None:
        """settlement 表无 is_active 字段（FB3 编译期保证）。"""
        assert not hasattr(Settlement, "is_active")

    async def test_service_has_no_delete_method(self) -> None:
        """service 不提供删除接口（FB3）。"""
        assert not hasattr(SettlementService, "delete")
        assert not hasattr(SettlementService, "soft_delete")
