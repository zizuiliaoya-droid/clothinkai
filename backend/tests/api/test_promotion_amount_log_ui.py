"""推广列表页级 ``ui.actions.amount_log``（流程线 PR-4 收尾 L3）：「金额改动」页签与菜单只看这个标记，不看报价值。

标记用金额改动接口（``GET /api/promotions/{id}/amount-log``）同一个判定来算：
能读到就有标记、读不到（403）就没有；与本页有没有行、行上报价是不是 null 无关。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission, UserPermissionOverride

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

_NO_ROWS = "/api/promotions/?keyword=__no_such_promotion__"


async def _call(session: AsyncSession, user: Any, path: str) -> Any:
    """真实路由 + 用户的有效权限（照 test_promotion_ship_api._call）。"""
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
            return await c.get(path)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.mark.usefixtures("tenant_ctx")
class TestAmountLogFlag:
    @pytest.mark.parametrize(
        ("who", "revoke_quote", "expected"),
        [
            ("admin", False, True),
            ("pr_manager", False, True),
            ("pr", False, True),
            ("operations", False, False),
            # 个人撤销报价读权限的 PR：接口 403，标记也没有
            ("pr", True, False),
        ],
    )
    async def test_flag_matches_amount_log_endpoint(
        self,
        session: AsyncSession,
        tenant_a: Any,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        who: str,
        revoke_quote: bool,
        expected: bool,
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
        user = getattr(flow_users, who)
        if revoke_quote:
            perm = (
                await session.execute(
                    select(Permission).where(
                        Permission.scope == "field.promotion.quote_amount:read"
                    )
                )
            ).scalar_one()
            session.add(
                UserPermissionOverride(
                    tenant_id=tenant_a.id, user_id=user.id, permission_id=perm.id, effect="revoke"
                )
            )
            await session.flush()

        page = await _call(session, user, "/api/promotions/?page_size=100")
        assert page.status_code == 200, page.text
        flag = "amount_log" in page.json()["ui"]["actions"]
        log = await _call(session, user, f"/api/promotions/{promo.id}/amount-log")
        assert log.status_code in (200, 403), log.text
        assert flag is (log.status_code == 200)
        assert flag is expected
        if expected:
            assert page.json()["ui"]["actions"]["amount_log"] == {"state": "enabled"}

    @pytest.mark.parametrize(("who", "expected"), [("pr", True), ("operations", False)])
    async def test_flag_does_not_depend_on_rows(
        self, session: AsyncSession, flow_users: Any, who: str, expected: bool
    ) -> None:
        """本页一行都没有时标记照样按权限给（不从行上的报价值推）。"""
        page = await _call(session, getattr(flow_users, who), _NO_ROWS)
        assert page.status_code == 200, page.text
        assert page.json()["items"] == []
        assert ("amount_log" in page.json()["ui"]["actions"]) is expected
