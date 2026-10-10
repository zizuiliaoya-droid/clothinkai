"""7e R1 报表视图权限的路由层契约（报表视图设计第 4 节、5.1）。

真实路由 + 迁移 seed 的真实角色（``flow_users``）：

- 工作进度：``require_any_permission(read, read_own)``；只持 ``read_own`` 的 PR 只看到本人那一行
  （service 层 ``resolve_pr_scope`` 强制）；区间参数 ``preset`` / ``date_from`` / ``date_to``，
  旧参数 ``month`` 换算成整月；F3 聚合金额运营与管理员相同
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.urge_calculator import get_today

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

# 场景放在上个月：整月都在过去，日期与「今天」无关的计数稳定
_TODAY = get_today()
_M_HI = _TODAY.replace(day=1) - timedelta(days=1)
_M_LO = _M_HI.replace(day=1)
_MONTH = f"{_M_LO:%Y-%m}"
_WP = "/api/reports/work-progress"


async def _call(session: AsyncSession, user: Any, method: str, path: str, json: Any = None) -> Any:
    """真实路由（含 schema 校验）+ 用户的有效权限。"""
    from app.core.db import get_session
    from app.main import app
    from app.modules.auth.deps import get_current_perms, get_current_user_active
    from app.modules.auth.service import AuthService

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield session

    perms = await AuthService(session).load_effective_permissions(user.id)
    try:
        app.dependency_overrides[get_session] = _session_override
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: perms
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            return await c.request(method, path, json=json)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.fixture
async def seeded(
    flow_users: Any, product_factory: Any, blogger_factory: Any, promotion_factory: Any
) -> Any:
    """上个月：pr 两单、pr2 一单、未分配一单，都带样品成本（工作进度的 ``cost``）。"""
    style = await product_factory.style(style_code=f"RV{uuid4().hex[:8]}")
    blogger = await blogger_factory.blogger()

    async def promo(**kw: Any) -> None:
        await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            internal_code=f"RV{uuid4().hex[:10].upper()}",
            cooperation_date=_M_LO + timedelta(days=2),
            **kw,
        )

    await promo(pr=flow_users.pr, cost_snapshot=Decimal("30.00"), publish_status="已发布")
    await promo(pr=flow_users.pr, cost_snapshot=Decimal("12.50"))
    await promo(pr=flow_users.pr2, cost_snapshot=Decimal("40.00"))
    await promo(cost_snapshot=Decimal("7.00"))
    return flow_users


@pytest.mark.usefixtures("tenant_ctx")
class TestWorkProgress:
    async def test_pr_sees_only_self(self, session: AsyncSession, seeded: Any) -> None:
        resp = await _call(session, seeded.pr, "GET", f"{_WP}?month={_MONTH}")
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        assert [r["pr_id"] for r in rows] == [str(seeded.pr.id)]
        assert rows[0]["quote_count"] == 2

    async def test_finance_sees_all(self, session: AsyncSession, seeded: Any) -> None:
        resp = await _call(session, seeded.finance, "GET", f"{_WP}?month={_MONTH}")
        assert resp.status_code == 200, resp.text
        assert {r["pr_id"] for r in resp.json()} == {str(seeded.pr.id), str(seeded.pr2.id), None}

    async def test_month_equals_custom_range(self, session: AsyncSession, seeded: Any) -> None:
        by_month = await _call(session, seeded.pr_manager, "GET", f"{_WP}?month={_MONTH}")
        by_range = await _call(
            session,
            seeded.pr_manager,
            "GET",
            f"{_WP}?preset=custom&date_from={_M_LO}&date_to={_M_HI}",
        )
        assert by_month.status_code == by_range.status_code == 200
        assert by_month.json() == by_range.json()
        # 场景有效性：三行（pr / pr2 / 未分配）
        assert len(by_month.json()) == 3

    async def test_default_preset_is_this_month(self, session: AsyncSession, seeded: Any) -> None:
        bare = await _call(session, seeded.pr_manager, "GET", _WP)
        this_month = await _call(session, seeded.pr_manager, "GET", f"{_WP}?preset=this_month")
        assert bare.status_code == this_month.status_code == 200
        assert bare.json() == this_month.json()

    async def test_operations_aggregate_cost_equals_admin(
        self, session: AsyncSession, seeded: Any
    ) -> None:
        """F3：运营读不到逐单 ``cost_snapshot``，聚合的 ``cost`` / ``cpl`` 照样与管理员相同。"""
        ops = await _call(session, seeded.operations, "GET", f"{_WP}?month={_MONTH}")
        admin = await _call(session, seeded.admin, "GET", f"{_WP}?month={_MONTH}")
        assert ops.status_code == admin.status_code == 200
        assert ops.json() == admin.json()
        costs = {r["pr_id"]: r["cost"] for r in ops.json()}
        assert costs[str(seeded.pr.id)] == "42.50"

    async def test_warehouse_forbidden(self, session: AsyncSession, seeded: Any) -> None:
        resp = await _call(session, seeded.warehouse, "GET", f"{_WP}?month={_MONTH}")
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")

    @pytest.mark.parametrize(
        ("query", "code"),
        [
            ("preset=foo", "REPORT_INVALID_TIME_PRESET"),
            (
                f"preset=custom&date_from={_M_LO}&date_to={_M_LO + timedelta(days=367)}",
                "REPORT_INVALID_TIME_RANGE",
            ),
            (f"preset=custom&date_from={_M_HI}&date_to={_M_LO}", "REPORT_INVALID_TIME_RANGE"),
            ("month=2026-13", "REPORT_INVALID_TIME_RANGE"),
        ],
        ids=["bad_preset", "span_367", "from_after_to", "bad_month"],
    )
    async def test_invalid_range_422(
        self, session: AsyncSession, flow_users: Any, query: str, code: str
    ) -> None:
        resp = await _call(session, flow_users.pr_manager, "GET", f"{_WP}?{query}")
        assert (resp.status_code, resp.json()["code"]) == (422, code)

    async def test_span_366_ok(self, session: AsyncSession, flow_users: Any) -> None:
        hi = _M_LO + timedelta(days=366)
        resp = await _call(
            session,
            flow_users.pr_manager,
            "GET",
            f"{_WP}?preset=custom&date_from={_M_LO}&date_to={hi}",
        )
        assert resp.status_code == 200, resp.text
