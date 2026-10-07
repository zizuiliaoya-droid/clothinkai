"""7a-7 实际发布日期不能晚于今天（publish）。

「今天」按 Asia/Shanghai（get_today）；状态机先判，日期后判（plan D4）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from freezegun import freeze_time
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IllegalStateTransitionError
from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.exceptions import PublishDateInFutureError
from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import PromotionPublishRequest
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today

_URL = "https://www.xiaohongshu.com/note/7a7"


async def _setup(
    factory: Any,
    tenant_a: Any,
    admin_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
    **kw: Any,
) -> tuple[Any, Any]:
    user = await factory.user(tenant_a, roles=[admin_role])
    style = await product_factory.style()
    blogger = await blogger_factory.blogger()
    promotion = await promotion_factory.promotion(
        style=style, blogger=blogger, pr=user, brand_comment=True, **kw
    )
    return user, promotion


@pytest.mark.integration
@pytest.mark.asyncio
class TestPublishDateNotInFuture:
    async def test_today_is_allowed(
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
            user, promotion = await _setup(
                factory, tenant_a, admin_role, product_factory, blogger_factory, promotion_factory
            )
            today = get_today()
            resp = await PromotionService(session).publish(
                promotion.id,
                PromotionPublishRequest(publish_url=_URL, actual_publish_date=today),
                user,
            )
            assert resp.publish_status == "已发布"
            assert resp.actual_publish_date == today
        finally:
            tenant_id_ctx.reset(token)

    async def test_tomorrow_is_rejected_and_nothing_changes(
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
            user, promotion = await _setup(
                factory, tenant_a, admin_role, product_factory, blogger_factory, promotion_factory
            )
            tomorrow = get_today() + timedelta(days=1)
            with pytest.raises(PublishDateInFutureError) as exc_info:
                await PromotionService(session).publish(
                    promotion.id,
                    PromotionPublishRequest(publish_url=_URL, actual_publish_date=tomorrow),
                    user,
                )
            assert exc_info.value.code == "PUBLISH_DATE_IN_FUTURE"
            assert exc_info.value.status_code == 422
            assert exc_info.value.details["actual_publish_date"] == tomorrow.isoformat()

            row = await session.get(Promotion, promotion.id, populate_existing=True)
            assert row is not None
            assert row.publish_status == "未发布"
            assert row.settlement_status == "未核查"
            assert row.actual_publish_date is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_state_machine_checked_before_date(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """已发布的单 + 明天：报「状态不对」，不报日期错。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user, promotion = await _setup(
                factory,
                tenant_a,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
                publish_status="已发布",
                settlement_status="待核查",
            )
            tomorrow = get_today() + timedelta(days=1)
            with pytest.raises(IllegalStateTransitionError) as exc_info:
                await PromotionService(session).publish(
                    promotion.id,
                    PromotionPublishRequest(publish_url=_URL, actual_publish_date=tomorrow),
                    user,
                )
            assert exc_info.value.code == "ILLEGAL_STATE_TRANSITION"
        finally:
            tenant_id_ctx.reset(token)

    @freeze_time("2026-05-26 16:30:00")  # UTC 16:30 = 北京 05-27 00:30
    async def test_shanghai_boundary_allows_beijing_today(
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
            user, promotion = await _setup(
                factory, tenant_a, admin_role, product_factory, blogger_factory, promotion_factory
            )
            resp = await PromotionService(session).publish(
                promotion.id,
                PromotionPublishRequest(publish_url=_URL, actual_publish_date=date(2026, 5, 27)),
                user,
            )
            assert resp.actual_publish_date == date(2026, 5, 27)
        finally:
            tenant_id_ctx.reset(token)

    @freeze_time("2026-05-26 16:30:00")
    async def test_shanghai_boundary_rejects_beijing_tomorrow(
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
            user, promotion = await _setup(
                factory, tenant_a, admin_role, product_factory, blogger_factory, promotion_factory
            )
            with pytest.raises(PublishDateInFutureError) as exc_info:
                await PromotionService(session).publish(
                    promotion.id,
                    PromotionPublishRequest(
                        publish_url=_URL, actual_publish_date=date(2026, 5, 28)
                    ),
                    user,
                )
            assert exc_info.value.details["today"] == "2026-05-27"
        finally:
            tenant_id_ctx.reset(token)
