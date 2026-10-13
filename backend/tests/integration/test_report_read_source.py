"""读取入口收调用方给定的来源（7e R6 S1）。

BI 整页只判一次 ``freshness``，再把结果传给店铺日行、工作进度、投产单品三个入口：
给了 ``source`` 的入口不再自己判（否则一页里各块可能各选各的来源），没给照旧自己判。
另测 BI 目标篇数从 BI 的 SQL 搬出来的 ``TargetPlanningRepository.targets_by_pr``。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.report.production_service as production_mod
import app.modules.report.store_daily_service as store_mod
import app.modules.report.work_progress_service as work_mod
from app.core.security.field_permissions import FieldPermissionContext
from app.core.tenancy import tenant_id_ctx
from app.modules.report.advanced_repository import TargetPlanningRepository
from app.modules.report.pr_scope import PR_SCOPE_ALL
from app.modules.report.production_service import ProductionService
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.summary_read import ReportFreshness, SummaryReadRepository
from app.modules.report.work_progress_models import TargetPlanning
from app.modules.report.work_progress_service import WorkProgressService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

LO, HI = date(2026, 8, 20), date(2026, 9, 5)
_ALL_AMOUNTS = FieldPermissionContext(frozenset(), frozenset(), frozenset(), is_superuser=True)


class _Spy:
    """记下 freshness 被调了几次、每个 record_source 走了哪条路径。"""

    def __init__(self) -> None:
        self.freshness_calls = 0
        self.sources: list[tuple[str, str]] = []


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    rec = _Spy()
    real = SummaryReadRepository.freshness

    async def counting(
        self: SummaryReadRepository, tenant_id: Any, date_from: date, date_to: date
    ) -> ReportFreshness:
        rec.freshness_calls += 1
        return await real(self, tenant_id, date_from, date_to)

    monkeypatch.setattr(SummaryReadRepository, "freshness", counting)
    for mod in (production_mod, store_mod, work_mod):
        monkeypatch.setattr(mod, "record_source", lambda r, s: rec.sources.append((r, s)))
    return rec


async def _read_all(session: AsyncSession, tenant: Any, **kw: Any) -> None:
    await StoreDailyService(session).read_day_rows(tenant.id, LO, HI, "bi_store", **kw)
    await WorkProgressService(session).get_for_range(
        tenant.id,
        LO,
        HI,
        seasons=None,
        scope=PR_SCOPE_ALL,
        field_ctx=_ALL_AMOUNTS,
        **kw,
    )
    await ProductionService(session).get_goods_rows(
        tenant.id, (LO, HI), exclude_brushing=True, use_summary=True, **kw
    )


class TestGivenSource:
    @pytest.mark.parametrize("source", ["live", "summary"])
    async def test_given_source_skips_freshness(
        self, session: AsyncSession, tenant_a: Any, spy: _Spy, source: str
    ) -> None:
        await _read_all(session, tenant_a, source=source)
        assert spy.freshness_calls == 0
        assert [s for _, s in spy.sources] == [source] * 3

    async def test_without_source_each_entry_judges(
        self, session: AsyncSession, tenant_a: Any, spy: _Spy
    ) -> None:
        """没给 ``source``：行为不变，每个入口各判一次（区间未覆盖 → 实时）。"""
        await _read_all(session, tenant_a)
        assert spy.freshness_calls == 3
        assert [s for _, s in spy.sources] == ["live"] * 3

    async def test_use_summary_false_still_live(
        self, session: AsyncSession, tenant_a: Any, spy: _Spy
    ) -> None:
        await ProductionService(session).get_goods_rows(
            tenant_a.id, (LO, HI), exclude_brushing=True, use_summary=False
        )
        assert spy.freshness_calls == 0
        assert spy.sources == [("production", "live")]


class TestTargetsByPr:
    async def test_sums_months_touched_by_range(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
    ) -> None:
        """区间 08-20 ~ 09-05 碰到 8、9 两个月：两个月的目标整月相加，7 / 10 月和别租户不算。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr1 = await factory.user(tenant_a, roles=[pr_role])
            pr2 = await factory.user(tenant_a, roles=[pr_role])
            s1 = await product_factory.style()
            s2 = await product_factory.style()
            rows = [
                (pr1, s1, "2026-07", 100),
                (pr1, s1, "2026-08", 3),
                (pr1, s2, "2026-08", 4),
                (pr1, s1, "2026-09", 5),
                (pr1, s1, "2026-10", 200),
                (pr2, s2, "2026-09", 7),
            ]
            for pr, style, month, target in rows:
                session.add(
                    TargetPlanning(
                        tenant_id=tenant_a.id,
                        pr_id=pr.id,
                        style_id=style.id,
                        period_month=month,
                        min_target=target,
                    )
                )
            await session.flush()
        finally:
            tenant_id_ctx.reset(tok)
        tok = tenant_id_ctx.set(tenant_b.id)
        try:
            other_pr = await factory.user(tenant_b, roles=[])
            other_style = await product_factory.style(tenant_b)
            session.add(
                TargetPlanning(
                    tenant_id=tenant_b.id,
                    pr_id=other_pr.id,
                    style_id=other_style.id,
                    period_month="2026-08",
                    min_target=50,
                )
            )
            await session.flush()
        finally:
            tenant_id_ctx.reset(tok)

        repo = TargetPlanningRepository(session)
        assert await repo.targets_by_pr(tenant_id=tenant_a.id, date_from=LO, date_to=HI) == {
            pr1.id: 12,
            pr2.id: 7,
        }
        # 同月内的区间只算当月
        assert await repo.targets_by_pr(
            tenant_id=tenant_a.id, date_from=date(2026, 9, 1), date_to=date(2026, 9, 1)
        ) == {pr1.id: 5, pr2.id: 7}
        assert (
            await repo.targets_by_pr(
                tenant_id=tenant_a.id, date_from=date(2026, 11, 1), date_to=date(2026, 11, 30)
            )
            == {}
        )
