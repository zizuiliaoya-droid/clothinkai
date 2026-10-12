"""谈款接口的路由层契约（流程线 PR-3 S6；设计 7.1、7.2）。

- ``GET /goods-members`` 挂 ``negotiation:write``：PR / 主管 200；财务、运营 403 ``PERMISSION_DENIED``（进 service 之前）；
  路由要排在 ``/{negotiation_id}`` 前面，否则会被当成 id 解析成 422
- ``POST /`` 持 ``*`` → 403 ``FLOW_ACTION_FORBIDDEN``（``star_create``）
- ``POST /{id}/final-review`` 挂 ``negotiation_final:approve``：主管 403 ``PERMISSION_DENIED``；退回不写意见 422（schema 层）；
  ``POST /{id}/review`` 管理员 scope 够、service 规则拦 → 403 ``star_first_level``（S7）
- ``PUT /{id}/receiver``、``POST /{id}/confirm-receiver`` 挂 ``negotiation:write``；限长 / 电话 / 缺项 422，重复确认 409（S8）
- ``POST /{id}/copy`` 201；``POST /{id}/reassign`` 非管理员 403 ``admin_only``、原因限长（S9）
- ``GET /blogger/{id}/history`` 带 ``rejections`` / ``total_rejections``，``GET /blogger/{id}/others-in-progress`` 只回 3 个字段；
  两个都挂 ``negotiation:read``，仓库 403（S10）
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
                        "style_main_image_url": None,
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

    async def test_review_routes(
        self,
        session: AsyncSession,
        flow_users: Any,
        negotiation_factory: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """``final-review`` 缺 ``negotiation_final:approve`` → 403（进 service 之前）；退回不写意见 → 422；
        管理员定稿 200；管理员做第一级 → 403 ``star_first_level``。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        n = await negotiation_factory.negotiation(
            style=style,
            blogger=blogger,
            pr=flow_users.pr,
            status="待老板审核",
            reviewed_by=flow_users.pr_manager.id,
        )
        path = f"/api/negotiations/{n.id}/final-review"
        resp = await _call(session, flow_users.pr_manager, "POST", path, {"action": "approve"})
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")
        resp = await _call(session, flow_users.admin, "POST", path, {"action": "return"})
        assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_ERROR"), resp.text
        resp = await _call(session, flow_users.admin, "POST", path, {"action": "approve"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "待补收货地址"

        pending = await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr, status="主管待审"
        )
        resp = await _call(
            session,
            flow_users.admin,
            "POST",
            f"/api/negotiations/{pending.id}/review",
            {"action": "approve"},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["details"]["rule"] == "star_first_level"
        # 主管驳回不写意见 → 422（以前 ValueError 进 ctx，序列化时 500）
        resp = await _call(
            session,
            flow_users.pr_manager,
            "POST",
            f"/api/negotiations/{pending.id}/review",
            {"action": "reject"},
        )
        assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_ERROR"), resp.text

    async def test_receiver_routes(
        self,
        session: AsyncSession,
        flow_users: Any,
        negotiation_factory: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """``PUT /{id}/receiver`` 与 ``POST /{id}/confirm-receiver`` 挂 ``negotiation:write``：财务 403（进 service 之前）；
        超长 422（schema 层）、电话格式不对 422 ``INVALID_RECEIVER_PHONE``；确认缺项 422 ``FLOW_GATE_MISSING``；
        确认成功返回谈款带 ``promotion_id`` / ``promotion_internal_code``，再确认 409「已定稿，推广单已生成」（S8）。"""
        style = await product_factory.style()
        sku = await product_factory.sku(style)
        blogger = await blogger_factory.blogger()
        n = await negotiation_factory.negotiation(
            style=style,
            blogger=blogger,
            pr=flow_users.pr,
            status="待补收货地址",
            reviewed_by=flow_users.pr_manager.id,
            final_reviewed_by=flow_users.admin.id,
            items=[(style, sku)],
        )
        save, confirm = (
            f"/api/negotiations/{n.id}/receiver",
            f"/api/negotiations/{n.id}/confirm-receiver",
        )
        body = {
            "receiver_name": "张三",
            "receiver_phone": "13812345678",
            "receiver_address": "杭州某路 1 号",
        }
        for method, path in (("PUT", save), ("POST", confirm)):
            resp = await _call(session, flow_users.finance, method, path, body)
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), path
        resp = await _call(session, flow_users.pr, "PUT", save, {"receiver_name": "名" * 33})
        assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_ERROR"), resp.text
        resp = await _call(session, flow_users.pr, "PUT", save, {"receiver_phone": "12345"})
        assert (resp.status_code, resp.json()["code"]) == (422, "INVALID_RECEIVER_PHONE"), resp.text
        resp = await _call(session, flow_users.pr, "POST", confirm, {"receiver_name": "张三"})
        assert (resp.status_code, resp.json()["code"]) == (422, "FLOW_GATE_MISSING"), resp.text
        resp = await _call(session, flow_users.pr_manager, "POST", confirm, body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "已定稿"
        assert data["promotion_id"] and data["promotion_internal_code"]
        assert data["receiver_confirmed_by"] == str(flow_users.pr_manager.id)
        resp = await _call(session, flow_users.pr, "POST", confirm, body)
        assert resp.status_code == 409, resp.text
        assert resp.json()["code"] == "NEGOTIATION_STATE_CONFLICT"
        assert resp.json()["message"] == "已定稿，推广单已生成"

    async def test_copy_and_reassign_routes(
        self,
        session: AsyncSession,
        flow_users: Any,
        negotiation_factory: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """``POST /{id}/copy`` 挂 ``negotiation:write``，成功 201 返回新草稿；``POST /{id}/reassign`` 挂 ``negotiation:write``
        + service 判持 ``*``：主管 403 ``admin_only``、财务 403 ``PERMISSION_DENIED``，原因空白 / 超 500 字 422，管理员 200（S9）。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        rejected = await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr, status="审核驳回"
        )
        path = f"/api/negotiations/{rejected.id}/copy"
        resp = await _call(session, flow_users.finance, "POST", path)
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")
        resp = await _call(session, flow_users.pr, "POST", path)
        assert resp.status_code == 201, resp.text
        assert (resp.json()["status"], resp.json()["copied_from_id"]) == ("草稿", str(rejected.id))

        pending = await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr, status="主管待审"
        )
        path = f"/api/negotiations/{pending.id}/reassign"
        body = {"pr_id": str(flow_users.pr2.id), "reason": "原 PR 请假"}
        resp = await _call(session, flow_users.finance, "POST", path, body)
        assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED")
        resp = await _call(session, flow_users.pr_manager, "POST", path, body)
        assert resp.status_code == 403, resp.text
        assert resp.json()["details"]["rule"] == "admin_only"
        for reason in ("   ", "因" * 501):
            resp = await _call(session, flow_users.admin, "POST", path, {**body, "reason": reason})
            assert (resp.status_code, resp.json()["code"]) == (422, "VALIDATION_ERROR"), resp.text
        resp = await _call(session, flow_users.admin, "POST", path, body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["pr_id"] == str(flow_users.pr2.id)

    async def test_blogger_routes(
        self,
        session: AsyncSession,
        flow_users: Any,
        negotiation_factory: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        """博主卡片两个读接口（S10）：PR 看得到别的 PR 的驳回记录（无 id）与在谈的单（只有 3 个字段）；
        ``exclude_negotiation_id`` 生效；运营服务费 null；仓库没有 ``negotiation:read`` → 403。"""
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        rejected = await negotiation_factory.negotiation(
            style=style,
            blogger=blogger,
            pr=flow_users.pr2,
            status="审核驳回",
            review_opinion="不合适",
        )
        pending = await negotiation_factory.negotiation(
            style=style, blogger=blogger, pr=flow_users.pr2, status="主管待审"
        )
        history = f"/api/negotiations/blogger/{blogger.id}/history"
        others = f"/api/negotiations/blogger/{blogger.id}/others-in-progress"

        resp = await _call(session, flow_users.pr, "GET", history)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_rejections"] == 1
        assert [(r["negotiation_id"], r["opinion"]) for r in body["rejections"]] == [
            (None, "不合适")
        ]
        resp = await _call(session, flow_users.operations, "GET", history)
        assert resp.status_code == 200, resp.text
        [row] = resp.json()["rejections"]
        assert (row["negotiation_id"], row["quote_amount"]) == (str(rejected.id), None)

        resp = await _call(session, flow_users.pr, "GET", others)
        assert resp.status_code == 200, resp.text
        [item] = resp.json()
        assert set(item) == {"pr_name", "status", "updated_at"}
        assert item["status"] == "主管待审"
        resp = await _call(
            session, flow_users.pr, "GET", f"{others}?exclude_negotiation_id={pending.id}"
        )
        assert (resp.status_code, resp.json()) == (200, [])

        for path in (history, others):
            resp = await _call(session, flow_users.warehouse, "GET", path)
            assert (resp.status_code, resp.json()["code"]) == (403, "PERMISSION_DENIED"), path
