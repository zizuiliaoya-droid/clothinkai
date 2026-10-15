"""7e R7 S3：推广单「是否爆文」按平台阈值判（报表视图设计 2.7、第 8 节「平台指标」）。

配置用与默认值结论不同的一组：小红书 2000、抖音 500（默认小红书 1000、抖音 10000）。

场景（同一租户，都是启用的单）：
- 小红书 1500 赞 → 默认是爆文，配置后不是
- 抖音 800 赞 → 默认不是，配置后是
- 平台录错（``YT7612472372899``）1000 / 999 赞 → 不在 5 个平台里，两种情况都按 1000 判
- 点赞为空 → 两种情况都不是（``is_hit=false`` 那一侧）

列表的 ``is_hit`` 筛选 true / false 恰好是 Python 判定的划分；列表行与详情的 ``is_hit`` 一致。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.metric_config.models import PromotionPlatformSetting
from app.modules.metric_config.settings import DEFAULT_LIKE_COEFFICIENTS, PlatformMetricSettings
from app.modules.promotion.metrics_calculator import calculate_is_hit
from app.modules.promotion.schemas import PromotionListFilters
from app.modules.promotion.service import PromotionService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_ODD_PLATFORM = "YT7612472372899"
_THRESHOLDS = {"小红书": 2000, "抖音": 500}

# internal_code → (平台, 点赞)
_CASES: dict[str, tuple[str, int | None]] = {
    "HIT-XHS-1500": ("小红书", 1500),
    "HIT-DY-800": ("抖音", 800),
    "HIT-ODD-1000": (_ODD_PLATFORM, 1000),
    "HIT-ODD-999": (_ODD_PLATFORM, 999),
    "HIT-XHS-NONE": ("小红书", None),
}


async def _configure(session: AsyncSession, tenant: Any) -> None:
    tok = tenant_id_ctx.set(tenant.id)  # 插入钩子校验 tenant_id 与上下文一致
    try:
        for platform, threshold in _THRESHOLDS.items():
            session.add(
                PromotionPlatformSetting(
                    tenant_id=tenant.id,
                    platform=platform,
                    like_coefficient=DEFAULT_LIKE_COEFFICIENTS[platform],
                    hit_threshold=threshold,
                )
            )
        await session.flush()
    finally:
        tenant_id_ctx.reset(tok)


async def _seed(
    tenant: Any,
    factory: Any,
    admin_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> tuple[Any, dict[str, UUID]]:
    admin = await factory.user(tenant, roles=[admin_role])
    style = await product_factory.style()
    blogger = await blogger_factory.blogger(follower_count=50_000)
    ids: dict[str, UUID] = {}
    for code, (platform, likes) in _CASES.items():
        p = await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            platform=platform,
            like_count=likes,
            internal_code=code,
        )
        ids[code] = p.id
    return admin, ids


async def _observe(session: AsyncSession, admin: Any, ids: dict[str, UUID]) -> dict[str, Any]:
    svc = PromotionService(session)
    by_id = {v: k for k, v in ids.items()}

    async def _codes(is_hit: bool | None) -> dict[str, bool]:
        page = await svc.list_promotions(
            filters=PromotionListFilters(is_hit=is_hit), page=1, page_size=100, user=admin
        )
        return {by_id[i.id]: i.is_hit for i in page.items if i.id in by_id}

    listed = await _codes(None)
    detail = {code: (await svc.get_promotion(pid, admin)).is_hit for code, pid in ids.items()}
    return {
        "listed": listed,
        "detail": detail,
        "hit": set(await _codes(True)),
        "not_hit": set(await _codes(False)),
    }


def _expected(settings: PlatformMetricSettings) -> dict[str, bool]:
    return {
        code: calculate_is_hit(platform=platform, like_count=likes, settings=settings)
        for code, (platform, likes) in _CASES.items()
    }


class TestIsHitByPlatform:
    async def test_configured_thresholds(
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
            admin, ids = await _seed(
                tenant_a, factory, admin_role, product_factory, blogger_factory, promotion_factory
            )
            await _configure(session, tenant_a)
            got = await _observe(session, admin, ids)
        finally:
            tenant_id_ctx.reset(token)

        expected = {
            "HIT-XHS-1500": False,
            "HIT-DY-800": True,
            "HIT-ODD-1000": True,
            "HIT-ODD-999": False,
            "HIT-XHS-NONE": False,
        }
        configured = PlatformMetricSettings.from_rows(
            (p, DEFAULT_LIKE_COEFFICIENTS[p], t) for p, t in _THRESHOLDS.items()
        )
        # 场景有效性：配置后与默认值的结论确实不同（小红书、抖音两张翻转）
        assert _expected(configured) == expected
        assert _expected(PlatformMetricSettings.defaults()) != expected
        assert got["listed"] == expected
        assert got["detail"] == expected
        assert got["hit"] == {c for c, v in expected.items() if v}
        assert got["not_hit"] == {c for c, v in expected.items() if not v}
        assert got["hit"] | got["not_hit"] == set(_CASES)

    async def test_defaults_without_rows(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """不写配置行 = 默认值：小红书 1000、抖音 10000、录错的平台 1000。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            admin, ids = await _seed(
                tenant_a, factory, admin_role, product_factory, blogger_factory, promotion_factory
            )
            got = await _observe(session, admin, ids)
        finally:
            tenant_id_ctx.reset(token)

        expected = {
            "HIT-XHS-1500": True,
            "HIT-DY-800": False,
            "HIT-ODD-1000": True,
            "HIT-ODD-999": False,
            "HIT-XHS-NONE": False,
        }
        assert _expected(PlatformMetricSettings.defaults()) == expected
        assert got["listed"] == expected
        assert got["detail"] == expected
        assert got["hit"] == {c for c, v in expected.items() if v}
        assert got["not_hit"] == {c for c, v in expected.items() if not v}
