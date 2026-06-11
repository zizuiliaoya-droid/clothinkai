"""U04 publish 集成测试（EP05-S07 + 跨状态机推进 + PromotionPublished 事件）。"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.exceptions import StateTransitionConflictError
from app.modules.promotion.schemas import PromotionPublishRequest
from app.modules.promotion.service import PromotionService


@pytest.mark.integration
@pytest.mark.asyncio
class TestPublish:
    async def test_publish_advances_settlement(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """EP05-S07: publish → publish_status=已发布 + settlement_status=待核查."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=user
            )
            svc = PromotionService(session)
            response = await svc.publish(
                promotion.id,
                PromotionPublishRequest(
                    publish_url="https://www.xiaohongshu.com/note/abc",
                    actual_publish_date=date(2026, 5, 28),
                ),
                user,
            )
            assert response.publish_status == "已发布"
            assert response.settlement_status == "待核查"
            assert response.publish_url == "https://www.xiaohongshu.com/note/abc"
        finally:
            tenant_id_ctx.reset(token)

    async def test_publish_emits_event(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        event_capture: list[Any],
    ) -> None:
        """publish 时发 PromotionPublished 事件（通知类）."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=user
            )
            svc = PromotionService(session)
            await svc.publish(
                promotion.id,
                PromotionPublishRequest(
                    publish_url="https://www.xiaohongshu.com/note/x",
                    actual_publish_date=date(2026, 5, 28),
                ),
                user,
            )
            event_types = [e.event_type for e in event_capture]
            assert "PromotionPublished" in event_types
        finally:
            tenant_id_ctx.reset(token)

    async def test_publish_already_published_raises(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=user,
                publish_status="已发布",
            )
            svc = PromotionService(session)
            from app.core.exceptions import IllegalStateTransitionError
            with pytest.raises(IllegalStateTransitionError):
                await svc.publish(
                    promotion.id,
                    PromotionPublishRequest(
                        publish_url="https://x.com/n",
                        actual_publish_date=date(2026, 5, 28),
                    ),
                    user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_publish_other_tenant_returns_no_match(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """FB7: 跨租户 publish → 因 tenant_id WHERE 子句返回 0 行 → StateTransitionConflictError."""
        # 租户 A 的 promotion
        token_a = tenant_id_ctx.set(tenant_a.id)
        try:
            user_a = await factory.user(tenant_a, roles=[admin_role])
            style_a = await product_factory.style(tenant=tenant_a)
            blogger_a = await blogger_factory.blogger(tenant=tenant_a)
            promotion_a = await promotion_factory.promotion(
                style=style_a, blogger=blogger_a, pr=user_a, tenant=tenant_a
            )
        finally:
            tenant_id_ctx.reset(token_a)

        # 切换到租户 B 试图 publish A 的 promotion
        token_b = tenant_id_ctx.set(tenant_b.id)
        try:
            user_b = await factory.user(tenant_b, roles=[admin_role])
            svc = PromotionService(session)
            with pytest.raises(StateTransitionConflictError):
                await svc.publish(
                    promotion_a.id,
                    PromotionPublishRequest(
                        publish_url="https://x.com/n",
                        actual_publish_date=date(2026, 5, 28),
                    ),
                    user_b,
                )
        finally:
            tenant_id_ctx.reset(token_b)
