"""汇总表两个端点的行为：数据新鲜度、手动刷新的上沿。

直接调端点函数（与 ``test_daily_data_filters`` 同样的写法）：要验的是端点自己加的逻辑
——预设区间解析、``can_refresh`` 按真实权限判断、刷新区间截到今天——不是路由与鉴权
（那部分在 ``tests/api`` 的契约测试里）。
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.permissions import EffectivePermissions
from app.modules.promotion.urge_calculator import get_today
from app.modules.report.advanced_api import get_summary_freshness, refresh_summaries
from app.modules.report.summary_refresh_service import SummaryRefreshService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _perms(*scopes: str) -> EffectivePermissions:
    return EffectivePermissions(user_id="t", scopes=frozenset(scopes))


async def _coverage_days(session: AsyncSession, tenant_id: Any) -> list[Any]:
    rows = await session.execute(
        text(
            "SELECT stat_date FROM report_summary_coverage "
            "WHERE tenant_id = :t ORDER BY stat_date"
        ),
        {"t": tenant_id},
    )
    return list(rows.scalars().all())


class TestFreshnessEndpoint:
    async def test_preset_is_resolved_to_concrete_dates(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        """前端在预设模式下没有具体日期，刷新按钮要用端点解析出来的这一对。"""
        today = get_today()
        out = await get_summary_freshness(
            SimpleNamespace(tenant_id=tenant_a.id), _perms("*"), session, preset="last_7d"
        )
        assert (out.date_from, out.date_to) == (today - timedelta(days=6), today)
        # 没刷过 → 实时
        assert out.source == "live"
        assert out.data_as_of is None

    async def test_covered_range_reports_summary_and_refresh_time(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        today = get_today()
        lo = today - timedelta(days=6)
        await SummaryRefreshService(session).refresh(
            tenant_id=tenant_a.id, date_from=lo, date_to=today
        )
        await session.commit()

        out = await get_summary_freshness(
            SimpleNamespace(tenant_id=tenant_a.id), _perms("*"), session, preset="last_7d"
        )
        assert out.source == "summary"
        assert out.data_as_of is not None

    @pytest.mark.parametrize(
        ("scopes", "expected"),
        [
            (("*",), True),
            (("report.summary:refresh",), True),
            # operations / pr_manager 的实际权限：能看报表，不能刷新
            (("report.*:read",), False),
            (("report.production:read", "report.store_daily:write"), False),
        ],
    )
    async def test_can_refresh_follows_real_permission(
        self,
        session: AsyncSession,
        tenant_a: Any,
        scopes: tuple[str, ...],
        expected: bool,
    ) -> None:
        """前端只拿得到角色、算不了 scope，按钮显不显示全靠这个字段。"""
        out = await get_summary_freshness(
            SimpleNamespace(tenant_id=tenant_a.id), _perms(*scopes), session, preset="last_7d"
        )
        assert out.can_refresh is expected


class TestManualRefreshUpperBound:
    async def test_future_days_are_not_covered(self, session: AsyncSession, tenant_a: Any) -> None:
        """区间伸进未来时只刷到今天：未来的日子记了覆盖，读取侧就会改读一张空的汇总表。"""
        today = get_today()
        out = await refresh_summaries(
            SimpleNamespace(tenant_id=tenant_a.id),
            SummaryRefreshService(session),
            session,
            date_from=today - timedelta(days=2),
            date_to=today + timedelta(days=5),
        )
        assert out["date_to"] == str(today)
        days = await _coverage_days(session, tenant_a.id)
        assert days == [today - timedelta(days=i) for i in (2, 1, 0)]

    async def test_entirely_future_range_is_a_noop(
        self, session: AsyncSession, tenant_a: Any
    ) -> None:
        today = get_today()
        out = await refresh_summaries(
            SimpleNamespace(tenant_id=tenant_a.id),
            SummaryRefreshService(session),
            session,
            date_from=today + timedelta(days=1),
            date_to=today + timedelta(days=3),
        )
        assert out["skipped"] == "future"
        assert await _coverage_days(session, tenant_a.id) == []
