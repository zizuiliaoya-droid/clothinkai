"""谈款接口的路由层契约（流程线 PR-3 S6；设计 7.1、7.2）。

- ``GET /goods-members`` 挂 ``negotiation:write``：PR / 主管 200；财务、运营 403 ``PERMISSION_DENIED``（进 service 之前）；
  路由要排在 ``/{negotiation_id}`` 前面，否则会被当成 id 解析成 422
- ``POST /`` 持 ``*`` → 403 ``FLOW_ACTION_FORBIDDEN``（``star_create``）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx

pytestmark = [pytest.mark.api, pytest.mark.asyncio]


async def _call(session: AsyncSession, user: Any, method: str, path: str, json: Any = None) -> Any:
    """真实路由（含 schema 校验）+ 用户的有效权限（同 ``test_promotion_ship_api``）。"""
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


@pytest.mark.usefixtures("tenant_ctx")
class TestNegotiationRoutes:
    async def test_goods_members_scope(
        self, session: AsyncSession, flow_users: Any, product_factory: Any
    ) -> None:
        style = await product_factory.style(short_name="短裙", style_name="短裙全称")
        path = f"/api/negotiations/goods-members?style_id={style.id}"
        for who in ("pr", "pr_manager"):
            resp = await _call(session, getattr(flow_users, who), "GET", path)
            assert resp.status_code == 200, (who, resp.text)
            assert resp.json() == {
                "goods_main_id": None,
                "members": [
                    {
                        "style_id": str(style.id),
                        "display_short_name": "短裙",
                        "goods_title": "短裙全称",
                    }
                ],
            }
        for who in ("finance", "operations", "warehouse"):
            resp = await _call(session, getattr(flow_users, who), "GET", path)
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), who

    async def test_admin_create_forbidden(
        self, session: AsyncSession, flow_users: Any, product_factory: Any, blogger_factory: Any
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        resp = await _call(
            session,
            flow_users.admin,
            "POST",
            "/api/negotiations/",
            {"blogger_id": str(blogger.id), "style_id": str(style.id), "cooperation_mode": "寄拍"},
        )
        assert resp.status_code == 403, resp.text
        body = resp.json()
        assert body["code"] == "FLOW_ACTION_FORBIDDEN"
        assert body["details"]["rule"] == "star_create"
