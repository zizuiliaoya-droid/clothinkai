"""流程线 PR-2 推广单发货接口（``promotion/shipping_api.py``）的路由层契约（设计 7.1、7.3、9.3 A「权限通配」）。

端点 scope 留在路由层：缺 ``promotion_ship:push`` 在进 service 之前 403 ``PERMISSION_DENIED``。
PR 持 ``promotion.*:*``、运营持 ``promotion.*:read``——``promotion_ship`` 是独立一级域，通配捞不到（A2）。
状态机 / 矩阵 / ★ 的顺序在 ``tests/integration/test_promotion_shipping.py``。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx

pytestmark = [pytest.mark.api, pytest.mark.asyncio]

_SHIP_PATHS = ("ship/include", "ship/push", "ship/withdraw")


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


def _body(action: str) -> dict[str, Any]:
    return {"reason": "地址写错"} if action == "ship/withdraw" else {}


class TestContract:
    @pytest.mark.parametrize("action", _SHIP_PATHS)
    async def test_requires_auth(self, action: str) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(f"/api/promotions/{uuid4()}/{action}", json=_body(action))
        assert resp.status_code == 401

    async def test_openapi_exposes_ship_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            paths = (await ac.get("/api/openapi.json")).json()["paths"]
        for action in _SHIP_PATHS:
            assert f"/api/promotions/{{promotion_id}}/{action}" in paths, action


@pytest.mark.usefixtures("tenant_ctx")
class TestScopes:
    async def _promotion(
        self,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        ship_status: str | None,
    ) -> Any:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        return await promotion_factory.promotion(
            style=style, blogger=blogger, pr=flow_users.pr, ship_status=ship_status
        )

    @pytest.mark.parametrize(
        ("action", "ship_status"),
        [("ship/include", None), ("ship/push", "待发货"), ("ship/withdraw", "待打单")],
    )
    @pytest.mark.parametrize("who", ["pr", "operations", "finance", "warehouse"])
    async def test_without_push_scope_403_before_service(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        action: str,
        ship_status: str | None,
        who: str,
    ) -> None:
        """PR（``promotion.*:*``）、运营（``promotion.*:read``）、财务、仓库调发货动作一律 403 PERMISSION_DENIED，
        单据状态不动（A2：把 scope 挂到 promotion 域下，PR 这几格会被通配放行）。"""
        promo = await self._promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, ship_status
        )
        resp = await _call(
            session,
            getattr(flow_users, who),
            "POST",
            f"/api/promotions/{promo.id}/{action}",
            _body(action),
        )
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), resp.text
        got = (
            await session.execute(
                sa_text("SELECT ship_status FROM promotion WHERE id = :p"), {"p": promo.id}
            )
        ).scalar_one()
        assert got == ship_status

    async def test_manager_include_then_push_then_withdraw(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        style = await product_factory.style()
        sku = await product_factory.sku(style)
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
        base = f"/api/promotions/{promo.id}"
        manager = flow_users.pr_manager

        included = await _call(session, manager, "POST", f"{base}/ship/include", {})
        assert included.status_code == 200, included.text
        assert included.json()["ship_status"] == "待发货"
        # 缺项都在弹窗里补：按钮可点，missing 照给
        push_ui = included.json()["ui"]["actions"]["ship_push"]
        assert push_ui["state"] == "enabled"
        assert [m["key"] for m in push_ui["missing"]] == [
            "receiver_name",
            "receiver_phone",
            "receiver_address",
            "goods_items",
        ]

        # 缺项会让 service rollback（ORM 对象全部过期）：前置数据先提交，id 先取出来，之后刷新账号
        item = {"style_id": str(style.id), "sku_id": str(sku.id)}
        await session.commit()
        missing = await _call(session, manager, "POST", f"{base}/ship/push", {})
        assert missing.status_code == 422
        assert missing.json()["code"] == "FLOW_GATE_MISSING"
        assert missing.json()["details"]["missing"] == push_ui["missing"]
        await session.refresh(manager)

        pushed = await _call(
            session,
            manager,
            "POST",
            f"{base}/ship/push",
            {
                "items": [item],
                "receiver_name": "张三",
                "receiver_phone": "138 1234 5678",
                "receiver_address": "杭州某路 1 号",
            },
        )
        assert pushed.status_code == 200, pushed.text
        data = pushed.json()
        assert data["ship_status"] == "待打单"
        assert data["receiver_phone"] == "13812345678"
        assert data["ship_pushed_at"] is not None
        assert data["ship_pushed_by_name"]

        again = await _call(session, manager, "POST", f"{base}/ship/push", {})
        assert (again.status_code, again.json()["code"]) == (422, "ILLEGAL_STATE_TRANSITION")

        withdrawn = await _call(
            session, manager, "POST", f"{base}/ship/withdraw", {"reason": "收件人改了"}
        )
        assert withdrawn.status_code == 200, withdrawn.text
        assert withdrawn.json()["ship_status"] == "待发货"

    @pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * 501}])
    async def test_withdraw_reason_required(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        body: dict[str, Any],
    ) -> None:
        promo = await self._promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, "待打单"
        )
        resp = await _call(
            session,
            flow_users.pr_manager,
            "POST",
            f"/api/promotions/{promo.id}/ship/withdraw",
            body,
        )
        assert resp.status_code == 422, resp.text

    async def test_push_items_capped_at_10(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        promo = await self._promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, "待发货"
        )
        items = [{"style_id": str(uuid4()), "sku_id": str(uuid4())} for _ in range(11)]
        resp = await _call(
            session,
            flow_users.pr_manager,
            "POST",
            f"/api/promotions/{promo.id}/ship/push",
            {"items": items},
        )
        assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_ERROR")

    async def test_warehouse_cannot_read_promotion_list(
        self, session: AsyncSession, flow_users: Any
    ) -> None:
        """仓库 060 起收回 ``promotion:read``：推广通用列表 403（仓库页走 ``/api/warehouse``）。"""
        resp = await _call(session, flow_users.warehouse, "GET", "/api/promotions/")
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")

    async def test_list_ship_status_param(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        history = await self._promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, None
        )
        pending = await self._promotion(
            flow_users, product_factory, blogger_factory, promotion_factory, "待发货"
        )
        user = flow_users.pr_manager
        none = await _call(session, user, "GET", "/api/promotions/?ship_status=none&page_size=100")
        assert none.status_code == 200, none.text
        ids = {p["id"] for p in none.json()["items"]}
        assert str(history.id) in ids and str(pending.id) not in ids
        bad = await _call(session, user, "GET", "/api/promotions/?ship_status=%E5%BE%85%E6%8E%A8")
        assert bad.status_code == 422
