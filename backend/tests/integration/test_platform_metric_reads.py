"""7e R7 S2：折算点赞的系数从租户配置读（报表视图设计 2.8、第 8 节「平台指标」）。

配置用与默认值结论不同的一组（抖音 0.5、小红书 2.0；默认抖音 0.1、小红书 1.0），
每个读取点都要按新系数出数：工作进度实时与刷新后的汇总、发文进度摘要 / 卡片 / 按 PR、
推广详情的 ``effective_like_count`` / ``cpl``、谈款 hover 卡的 ``cpl``。不写配置行 = 默认值。

场景（同一 PR、同一款、9 月内，都已发布且录过 7 天数据）：
- 抖音 1500 赞、服务费 300 → 默认折算 150、配置后 750
- 小红书 101 赞、服务费 202 → 默认 101、配置后 202
- 平台录错（``YT7612472372899``）7 赞 → 不在 5 个平台里，两种情况都按 1 折算 = 7
合计：默认 258，配置后 959。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.field_permissions import FieldPermissionContext
from app.core.tenancy import tenant_id_ctx
from app.modules.metric_config.models import PromotionPlatformSetting
from app.modules.metric_config.settings import DEFAULT_HIT_THRESHOLDS
from app.modules.negotiation.service import NegotiationService
from app.modules.promotion.service import PromotionService
from app.modules.report.pr_scope import PR_SCOPE_ALL
from app.modules.report.service import PublishProgressService
from app.modules.report.summary_models import PrWorkProgressSummary
from app.modules.report.summary_refresh_service import SummaryRefreshService
from app.modules.report.work_progress_service import WorkProgressService

pytestmark = pytest.mark.asyncio

_SUPER_CTX = FieldPermissionContext(frozenset(), frozenset(), frozenset(), is_superuser=True)
_LO, _HI = date(2026, 9, 1), date(2026, 9, 30)
_ODD_PLATFORM = "YT7612472372899"

# 与默认值结论不同的一组：抖音 0.1 → 0.5、小红书 1.0 → 2.0（其余平台不写行，按默认）
_COEFS = {"抖音": Decimal("0.500"), "小红书": Decimal("2.000")}


async def _configure(session: AsyncSession, tenant: Any) -> None:
    tok = tenant_id_ctx.set(tenant.id)  # 插入钩子校验 tenant_id 与上下文一致
    try:
        for platform, coef in _COEFS.items():
            session.add(
                PromotionPlatformSetting(
                    tenant_id=tenant.id,
                    platform=platform,
                    like_coefficient=coef,
                    hit_threshold=DEFAULT_HIT_THRESHOLDS[platform],
                )
            )
        await session.flush()
    finally:
        tenant_id_ctx.reset(tok)


async def _seed(
    session: AsyncSession,
    tenant: Any,
    factory: Any,
    admin_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> dict[str, Any]:
    admin = await factory.user(tenant, roles=[admin_role])
    pr = await factory.user(tenant)
    style = await product_factory.style()
    blogger = await blogger_factory.blogger(follower_count=50_000)
    recorded = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)
    common = {
        "style": style,
        "blogger": blogger,
        "pr": pr,
        "publish_status": "已发布",
        "metrics_recorded_at": recorded,
    }
    douyin = await promotion_factory.promotion(
        **common,
        platform="抖音",
        like_count=1500,
        quote_amount=Decimal("300.00"),
        cooperation_date=date(2026, 9, 10),
    )
    xhs = await promotion_factory.promotion(
        **common,
        platform="小红书",
        like_count=101,
        quote_amount=Decimal("202.00"),
        cooperation_date=date(2026, 9, 12),
    )
    odd = await promotion_factory.promotion(
        **common,
        platform=_ODD_PLATFORM,
        like_count=7,
        quote_amount=Decimal("70.00"),
        cooperation_date=date(2026, 9, 14),
    )
    await session.flush()
    return {
        "admin": admin,
        "pr": pr,
        "style": style,
        "blogger": blogger,
        "douyin": douyin,
        "xhs": xhs,
        "odd": odd,
    }


async def _observe(session: AsyncSession, tenant: Any, seeded: dict[str, Any]) -> dict[str, Any]:
    """所有读取点的折算点赞与单赞成本。"""
    wp = WorkProgressService(session)
    live = await wp.get_for_range(
        tenant.id,
        _LO,
        _HI,
        seasons=None,
        scope=PR_SCOPE_ALL,
        field_ctx=_SUPER_CTX,
        source="live",
    )
    goods = await wp.get_goods(
        tenant.id,
        _LO,
        _HI,
        seasons=None,
        scope=PR_SCOPE_ALL,
        field_ctx=_SUPER_CTX,
        limit=20,
        offset=0,
    )
    weeks = await wp.get_weeks(
        tenant.id, _LO, _HI, seasons=None, scope=PR_SCOPE_ALL, field_ctx=_SUPER_CTX
    )
    pp = PublishProgressService(session)
    summary = await pp.get_summary(tenant.id, (_LO, _HI), pr_scope=PR_SCOPE_ALL)
    cards = await pp.get_cards(tenant.id, (_LO, _HI), page=1, page_size=20, pr_scope=PR_SCOPE_ALL)
    by_pr = await pp.get_detail_by_pr(
        tenant.id, seeded["style"].id, (_LO, _HI), pr_scope=PR_SCOPE_ALL
    )
    promo = PromotionService(session)
    detail = {
        key: await promo.get_promotion(seeded[key].id, seeded["admin"])
        for key in ("douyin", "xhs", "odd")
    }
    history = await NegotiationService(session).blogger_history(
        seeded["blogger"].id, seeded["admin"], limit=5
    )
    hist_cpl = {i.promotion_id: i.cpl for i in history.items}
    assert len(live) == 1 and len(cards.items) == 1 and len(by_pr) == 1
    assert goods.unassigned is not None  # 款式没归到商品：整段落在「未归属」那一行
    return {
        "live_like": live[0].like_count,
        "live_cpl": live[0].cpl,
        "goods_like": goods.unassigned.like_count,
        "weeks_like": sum(w.like_count for w in weeks.items),
        "summary_like": summary.like_count,
        "card_like": cards.items[0].like_count,
        "by_pr_like": by_pr[0].like_count,
        "effective": {k: v.effective_like_count for k, v in detail.items()},
        "detail_cpl": {k: v.cpl for k, v in detail.items()},
        "history_cpl": {k: hist_cpl[seeded[k].id] for k in ("douyin", "xhs", "odd")},
    }


async def _refresh_and_read(session: AsyncSession, tenant: Any) -> tuple[int, Decimal]:
    """刷新后汇总路径的 like_count，以及落盘的逐日精确值之和。"""
    await SummaryRefreshService(session).refresh(tenant_id=tenant.id, date_from=_LO, date_to=_HI)
    rows = await WorkProgressService(session).get_for_range(
        tenant.id,
        _LO,
        _HI,
        seasons=None,
        scope=PR_SCOPE_ALL,
        field_ctx=_SUPER_CTX,
        source="summary",
    )
    stored = (
        await session.execute(
            select(func.sum(PrWorkProgressSummary.like_count)).where(
                PrWorkProgressSummary.tenant_id == tenant.id
            )
        )
    ).scalar_one()
    assert len(rows) == 1
    return rows[0].like_count, Decimal(stored)


class TestLikeCoefficientFromConfig:
    async def test_configured_coefficients_apply_everywhere(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            await _configure(session, tenant_a)
            got = await _observe(session, tenant_a, seeded)

            # 1500 × 0.5 + 101 × 2 + 7 × 1 = 959（默认是 258）
            for key in (
                "live_like",
                "goods_like",
                "weeks_like",
                "summary_like",
                "card_like",
                "by_pr_like",
            ):
                assert got[key] == 959, key
            # 推广成本 300 + 202 + 70 = 572；572 / 959 = 0.59645… → 0.5965（默认 572 / 258 = 2.2171）
            assert got["live_cpl"] == Decimal("0.5965")
            assert got["effective"] == {"douyin": 750, "xhs": 202, "odd": 7}
            # 300 / 750、202 / 202、70 / 7
            expected_cpl = {
                "douyin": Decimal("0.4000"),
                "xhs": Decimal("1.0000"),
                "odd": Decimal("10.0000"),
            }
            assert got["detail_cpl"] == expected_cpl
            assert got["history_cpl"] == expected_cpl

            summary_like, stored = await _refresh_and_read(session, tenant_a)
            assert summary_like == 959
            assert stored == Decimal("959.0")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_no_rows_means_defaults(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """租户没有配置行（新租户）：按默认系数，与改动前数字一样，不报错。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            got = await _observe(session, tenant_a, seeded)

            # 1500 × 0.1 + 101 × 1 + 7 × 1 = 258
            for key in (
                "live_like",
                "goods_like",
                "weeks_like",
                "summary_like",
                "card_like",
                "by_pr_like",
            ):
                assert got[key] == 258, key
            assert got["live_cpl"] == Decimal("2.2171")  # 572 / 258 = 2.21705…
            assert got["effective"] == {"douyin": 150, "xhs": 101, "odd": 7}
            expected_cpl = {
                "douyin": Decimal("2.0000"),
                "xhs": Decimal("2.0000"),
                "odd": Decimal("10.0000"),
            }
            assert got["detail_cpl"] == expected_cpl
            assert got["history_cpl"] == expected_cpl

            summary_like, stored = await _refresh_and_read(session, tenant_a)
            assert summary_like == 258
            assert stored == Decimal("258.0")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_other_tenant_rows_do_not_leak(
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
        """别的租户配了系数，本租户仍按默认（读取显式按 tenant 过滤）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            await _configure(session, tenant_b)
            got = await _observe(session, tenant_a, seeded)
            assert got["live_like"] == 258
            assert got["effective"] == {"douyin": 150, "xhs": 101, "odd": 7}
        finally:
            tenant_id_ctx.reset(tok)
