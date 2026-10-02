"""催发分界天数读租户的 urge_config，不再写死 10 / 3。

以前只有企微扫描读配置；推广列表 / 详情的 urge_status、工作进度的四个催发计数、汇总表
刷新都写死 10 / 3 —— 后台把阈值改了，页面上的标签和报表纹丝不动，也不报错。

场景用一组与 10 / 3 结论不同的阈值（档期内 > 5 天、重要催发 ≤ 2 天）：

- 距预定发布 7 天：配置口径「档期内」，写死口径「催发」
- 距预定发布 3 天：配置口径「催发」，写死口径「重要催发」
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.schemas import PromotionListFilters
from app.modules.promotion.service import PromotionService
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.summary_models import PrWorkProgressSummary
from app.modules.report.summary_refresh_service import SummaryRefreshService
from app.modules.report.work_progress_service import WorkProgressService
from app.modules.urge.repository import UrgeConfigRepository

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TODAY = get_today()
COOP = TODAY - timedelta(days=1)


async def _configure(session: AsyncSession, tenant: Any) -> None:
    await UrgeConfigRepository(session).upsert(
        tenant_id=tenant.id,
        values={
            "no_publish_days": 5,
            "max_urge_times": 3,
            "max_overdue_days": 30,
            "urge_threshold_days": 5,
            "important_threshold_days": 2,
            "auto_scan_enabled": True,
        },
    )
    await session.flush()


async def _seed(
    session: AsyncSession,
    tenant: Any,
    factory: Any,
    admin_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> tuple[Any, dict[str, Any]]:
    await _configure(session, tenant)
    user = await factory.user(tenant, roles=[admin_role])
    style = await product_factory.style(tenant=tenant)
    blogger = await blogger_factory.blogger()
    promos = {}
    for label, days in (("in7", 7), ("in3", 3)):
        promos[label] = await promotion_factory.promotion(
            tenant=tenant,
            style=style,
            blogger=blogger,
            internal_code=f"UT{label.upper()}{uuid4().hex[:8].upper()}",
            cooperation_date=COOP,
            scheduled_publish_date=TODAY + timedelta(days=days),
        )
    return user, promos


class TestPromotionUrgeStatus:
    async def test_list_and_detail_follow_tenant_config(
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
            user, promos = await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            svc = PromotionService(session)

            page = await svc.list_promotions(
                filters=PromotionListFilters(), page=1, page_size=50, user=user
            )
            by_id = {p.id: p.urge_status for p in page.items}
            assert by_id[promos["in7"].id] == "档期内"
            assert by_id[promos["in3"].id] == "催发"

            # 详情走 Python 实现（不是列表的 SQL），同样要读配置
            assert (await svc.get_promotion(promos["in7"].id, user)).urge_status == "档期内"
            assert (await svc.get_promotion(promos["in3"].id, user)).urge_status == "催发"
        finally:
            tenant_id_ctx.reset(tok)


class TestWorkProgressUrgeCounts:
    async def test_live_and_summary_follow_tenant_config(
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
            await _seed(
                session,
                tenant_a,
                factory,
                admin_role,
                product_factory,
                blogger_factory,
                promotion_factory,
            )
            want = {"in_schedule_count": 1, "urge_count": 1, "important_urge_count": 0}

            # 实时路径（当月始终实时）
            rows = await WorkProgressService(session).get_for_month(tenant_a.id, f"{COOP:%Y-%m}")
            live = {k: sum(getattr(r, k) for r in rows) for k in want}
            assert live == want

            # 汇总刷新落盘的计数也按同一份配置算
            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=COOP, date_to=COOP
            )
            stored_row = (
                await session.execute(
                    select(
                        *(
                            func.coalesce(func.sum(getattr(PrWorkProgressSummary, k)), 0)
                            for k in want
                        )
                    ).where(
                        PrWorkProgressSummary.tenant_id == tenant_a.id,
                        PrWorkProgressSummary.stat_date == COOP,
                    )
                )
            ).one()
            stored = dict(zip(want, (int(v) for v in stored_row), strict=True))
            assert stored == want
        finally:
            tenant_id_ctx.reset(tok)
