"""U04 cancel + recall 集成测试（EP05-S08 / S09）。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IllegalStateTransitionError
from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.freight_rules import FreightValidationError
from app.modules.promotion.schemas import (
    PromotionCancelRequest,
    PromotionRecallStartRequest,
    PromotionRecallSuccessRequest,
)
from app.modules.promotion.service import PromotionService


@pytest.mark.integration
@pytest.mark.asyncio
class TestCancel:
    async def test_cancel_unpublished(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """EP05-S08: 未发布 → 已取消."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(style=style, blogger=blogger, pr=user)
            svc = PromotionService(session)
            response = await svc.cancel(
                promotion.id,
                PromotionCancelRequest(cancel_reason="临时撤档"),
                user,
            )
            assert response.publish_status == "已取消"
            assert response.cancel_reason == "临时撤档"
        finally:
            tenant_id_ctx.reset(token)

    async def test_cancel_already_published_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """已发布的不能直接 cancel（应走 recall）."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=user,
                publish_status="已发布",
            )
            svc = PromotionService(session)
            with pytest.raises(IllegalStateTransitionError):
                await svc.cancel(
                    promotion.id,
                    PromotionCancelRequest(cancel_reason="x"),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestRecall:
    async def test_start_recall_after_publish(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """EP05-S09: 已发布 → 启动召回（recall_status: 未召回 → 召回中）."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=user,
                publish_status="已发布",
            )
            svc = PromotionService(session)
            response = await svc.start_recall(
                promotion.id,
                PromotionRecallStartRequest(recall_reason="质量问题"),
                user,
            )
            assert response.recall_status == "召回中"
            assert response.recall_reason == "质量问题"
        finally:
            tenant_id_ctx.reset(token)

    async def test_start_recall_unshipped_suggests_cancel(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """C2只接受已发货或历史NULL；尚未发货提示直接取消。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=user, ship_status="待发货"
            )
            svc = PromotionService(session)
            with pytest.raises(FreightValidationError):
                await svc.start_recall(
                    promotion.id,
                    PromotionRecallStartRequest(recall_reason="召回"),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_recall_full_lifecycle(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        attachment_factory: Any,
    ) -> None:
        """召回中 → 召回失败 → 重新发起 → 召回成功."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=user,
                publish_status="已发布",
            )
            svc = PromotionService(session)

            await svc.start_recall(
                promotion.id, PromotionRecallStartRequest(recall_reason="召回"), user
            )
            r2 = await svc.recall_failure(promotion.id, user)
            assert r2.recall_status == "召回失败"

            await svc.start_recall(
                promotion.id, PromotionRecallStartRequest(recall_reason="再试"), user
            )
            shot = await attachment_factory.attachment(purpose="recall_waybill_screenshot")
            promotion.recall_waybill_attachment_id = shot.id
            await session.flush()
            r4 = await svc.recall_success(
                promotion.id,
                PromotionRecallSuccessRequest(return_shipping_fee=0, return_shipping_payer="到付"),
                user,
            )
            assert r4.recall_status == "召回成功"
        finally:
            tenant_id_ctx.reset(token)
