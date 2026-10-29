"""真实偏好表：当前用户列设置、筛选隔离与租户/用户隔离。"""

import pytest

from app.core.tenancy import tenant_id_ctx
from app.modules.report.preference_api import (
    PromotionColumnPreference,
    get_promotion_columns,
    save_promotion_columns,
)
from app.modules.report.user_preference_service import UserPreferenceService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_columns_roundtrip_reset_does_not_overwrite_filters(session, tenant_a, factory):
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        user = await factory.user(tenant_a)
        service = UserPreferenceService(session)
        filters = {"goods_id": "g", "season": ["春季"], "content_category": "单篇"}
        await service.save_filter(user, "promotion_list", filters)
        assert await get_promotion_columns(user, service) == PromotionColumnPreference()
        preference = PromotionColumnPreference(
            order=["stage", "content_category"], hidden=["quote_amount"]
        )
        assert await save_promotion_columns(user, service, preference) == {"ok": True}
        assert await get_promotion_columns(user, service) == preference
        assert await service.get_filter(user.id, "promotion_list") == filters
        await service.save_filter(user, "promotion_list", {})
        assert await get_promotion_columns(user, service) == preference
        await save_promotion_columns(user, service, PromotionColumnPreference())
        assert await get_promotion_columns(user, service) == PromotionColumnPreference()
    finally:
        tenant_id_ctx.reset(token)


async def test_columns_are_separate_for_users_and_tenants(session, tenant_a, tenant_b, factory):
    service = UserPreferenceService(session)
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        first = await factory.user(tenant_a)
        second = await factory.user(tenant_a)
        pref_a = PromotionColumnPreference(hidden=["quote_amount"])
        await save_promotion_columns(first, service, pref_a)
        assert await get_promotion_columns(second, service) == PromotionColumnPreference()
        tenant_id_ctx.set(tenant_b.id)
        third = await factory.user(tenant_b)
        assert await get_promotion_columns(third, service) == PromotionColumnPreference()
        pref_b = PromotionColumnPreference(hidden=["main_image"])
        await save_promotion_columns(third, service, pref_b)
        assert await get_promotion_columns(third, service) == pref_b
        tenant_id_ctx.set(tenant_a.id)
        assert await get_promotion_columns(first, service) == pref_a
        assert await get_promotion_columns(second, service) == PromotionColumnPreference()
    finally:
        tenant_id_ctx.reset(token)
