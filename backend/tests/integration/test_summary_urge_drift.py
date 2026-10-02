"""窗口外已覆盖日子的「催发漂移」补刷。

工作进度的档期内 / 催发 / 重要催发按刷新那一刻的「今天」落盘。窗口外的日子不再被每小时
刷新，排期还没到的未发布单会一直停在旧分类上 —— 数据一个字没动，变的只是日历。
``refresh_urge_drift`` 每小时把这类日子补刷一遍；这里验证名单选得对、补刷后与实时一致、
以及名单会自己收敛。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.report.summary_refresh_service as refresh_module
from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.urge_calculator import DEFAULT_TENANT_TZ, get_today
from app.modules.report.advanced_repository import WorkProgressRepository
from app.modules.report.summary_models import PrWorkProgressSummary
from app.modules.report.summary_read import SummaryReadRepository
from app.modules.report.summary_refresh_service import SummaryRefreshService
from app.modules.urge.service import UrgeService
from app.tasks.summary_tasks import REFRESH_WINDOW_DAYS, refresh_urge_drift

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TODAY = get_today()
WINDOW_LO = TODAY - timedelta(days=REFRESH_WINDOW_DAYS - 1)
OLD = TODAY - timedelta(days=60)  # 窗口外
STALE = TODAY - timedelta(days=30)  # 上一次刷新那天

_URGE_COLS = ("in_schedule_count", "urge_count", "important_urge_count", "overdue_count")


async def _stale_refresh(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, tenant: Any, day: date
) -> None:
    """模拟「这一天最后一次被刷新是在 STALE 那天」：按 STALE 的今天落盘，覆盖时间也拨回去。

    覆盖时间故意设在租户时区的 00:30（UTC 还是前一天）—— 判据里要是按 UTC 取日期，
    边界那张单就会被算错。
    """
    with monkeypatch.context() as m:
        m.setattr(refresh_module, "get_today", lambda: STALE)
        await SummaryRefreshService(session).refresh(
            tenant_id=tenant.id, date_from=day, date_to=day
        )
    refreshed_at = datetime.combine(STALE, time(0, 30), tzinfo=DEFAULT_TENANT_TZ).astimezone(UTC)
    await session.execute(
        text(
            "UPDATE report_summary_coverage SET refreshed_at = :at "
            "WHERE tenant_id = :t AND stat_date = :d"
        ),
        {"at": refreshed_at, "t": tenant.id, "d": day},
    )
    await session.flush()


async def _promo(
    promotion_factory: Any,
    tenant: Any,
    style: Any,
    blogger: Any,
    *,
    coop: date,
    sched: date | None,
    status: str = "未发布",
    active: bool = True,
) -> None:
    await promotion_factory.promotion(
        tenant=tenant,
        style=style,
        blogger=blogger,
        cooperation_date=coop,
        scheduled_publish_date=sched,
        publish_status=status,
        is_active=active,
        internal_code=f"DR{uuid4().hex[:10].upper()}",
    )


async def _stored_urge_counts(session: AsyncSession, tenant: Any, day: date) -> dict[str, int]:
    row = (
        await session.execute(
            select(
                *(func.coalesce(func.sum(getattr(PrWorkProgressSummary, c)), 0) for c in _URGE_COLS)
            )
            .where(PrWorkProgressSummary.tenant_id == tenant.id)
            .where(PrWorkProgressSummary.stat_date == day)
        )
    ).one()
    return dict(zip(_URGE_COLS, (int(v) for v in row), strict=True))


async def _live_urge_counts(session: AsyncSession, tenant: Any, day: date) -> dict[str, int]:
    rows = await WorkProgressRepository(session).aggregate_by_pr(
        tenant_id=tenant.id,
        date_from=day,
        date_to=day,
        today=TODAY,
        thresholds=await UrgeService(session).get_urge_thresholds(tenant.id),
    )
    return {c: sum(int(r[c]) for r in rows) for c in _URGE_COLS}


class TestDriftSelection:
    async def test_unsettled_promotions_are_picked_settled_are_not(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(tenant=tenant_a)
            blogger = await blogger_factory.blogger()
            # 每种情形各占一天，名单里出现哪天就知道是哪条规则在起作用
            d_future = OLD  # 排期在今天之后：会变
            d_boundary = OLD + timedelta(days=1)  # 排期 = 刷新那天：当时重要催发，之后超时
            d_was_overdue = OLD + timedelta(days=2)  # 排期 < 刷新那天：当时已超时，不会再变
            d_published = OLD + timedelta(days=3)  # 已发布：终态
            d_inactive = OLD + timedelta(days=4)  # 停用的单不进工作进度
            d_uncovered = OLD + timedelta(days=5)  # 没被覆盖：本来就走实时，不能刷（会冻结）
            d_in_window = TODAY - timedelta(days=3)  # 窗口内：每小时本来就会刷

            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=d_future,
                sched=TODAY + timedelta(days=2),
            )
            await _promo(promotion_factory, tenant_a, style, blogger, coop=d_boundary, sched=STALE)
            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=d_was_overdue,
                sched=STALE - timedelta(days=1),
            )
            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=d_published,
                sched=TODAY + timedelta(days=5),
                status="已发布",
            )
            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=d_inactive,
                sched=TODAY + timedelta(days=5),
                active=False,
            )
            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=d_uncovered,
                sched=TODAY + timedelta(days=5),
            )
            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=d_in_window,
                sched=TODAY + timedelta(days=5),
            )
            for d in (d_future, d_boundary, d_was_overdue, d_published, d_inactive, d_in_window):
                await _stale_refresh(session, monkeypatch, tenant_a, d)

            picked = await SummaryReadRepository(session).urge_drift_dates(
                tenant_a.id, before=WINDOW_LO
            )
            assert picked == [d_future, d_boundary]
        finally:
            tenant_id_ctx.reset(tok)


class TestDriftRefresh:
    async def test_refresh_brings_stale_counts_back_to_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(tenant=tenant_a)
            blogger = await blogger_factory.blogger()
            # STALE 那天看：档期内（还有 32 天）；今天看：重要催发（还有 2 天）
            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=OLD,
                sched=TODAY + timedelta(days=2),
            )
            # STALE 那天看：催发（还有 10 天）；今天看：超时
            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=OLD,
                sched=TODAY - timedelta(days=20),
            )
            await _stale_refresh(session, monkeypatch, tenant_a, OLD)

            before = await _stored_urge_counts(session, tenant_a, OLD)
            assert before == {
                "in_schedule_count": 1,
                "urge_count": 1,
                "important_urge_count": 0,
                "overdue_count": 0,
            }, "前置没造出「过时」的汇总行，后面的断言就没意义了"

            runs = await refresh_urge_drift(session, tenant_id=tenant_a.id, window_lo=WINDOW_LO)
            assert runs == [(OLD, OLD)]

            after = await _stored_urge_counts(session, tenant_a, OLD)
            assert after == await _live_urge_counts(session, tenant_a, OLD)
            assert after == {
                "in_schedule_count": 0,
                "urge_count": 0,
                "important_urge_count": 1,
                "overdue_count": 1,
            }
        finally:
            tenant_id_ctx.reset(tok)

    async def test_list_converges_once_everything_is_overdue(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """排期都已过的日子补刷一次就退出名单 —— 不会每小时刷同一批日子。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(tenant=tenant_a)
            blogger = await blogger_factory.blogger()
            await _promo(
                promotion_factory,
                tenant_a,
                style,
                blogger,
                coop=OLD,
                sched=TODAY - timedelta(days=20),
            )
            await _stale_refresh(session, monkeypatch, tenant_a, OLD)
            repo = SummaryReadRepository(session)

            assert await repo.urge_drift_dates(tenant_a.id, before=WINDOW_LO) == [OLD]
            await refresh_urge_drift(session, tenant_id=tenant_a.id, window_lo=WINDOW_LO)
            assert await repo.urge_drift_dates(tenant_a.id, before=WINDOW_LO) == []
        finally:
            tenant_id_ctx.reset(tok)
