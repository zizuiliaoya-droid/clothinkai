"""流程线 PR-5 S6：推广列表页级 ``ui.actions.create`` / ``import``、结款列表页级 ``ui.actions.import``（设计 7.5、8.4、8.6）。

- 推广列表：``create`` 持 ``promotion_create:write`` 才有（与 ``POST /api/promotions/`` 同一判定）；
  ``import`` 用导入站外推广（``manual_promotion``）的上传判定（``importer/access.py::can_write``）。
  主管、管理员有，PR、运营没有；个人授予 PR ``promotion_create:write`` 后两个都有
- 结款列表：``import`` 用结款单导入（``manual_settlement``）的上传判定，即 ``finance.settlement:pay``：
  财务、管理员有，主管没有
- 标记只看权限，本页一行都没有时照样给

HTTP 级，角色用 ``flow_users``（迁移 seed 的真实角色）。
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

_PROMO_NO_ROWS = "/api/promotions/?keyword=__no_such_promotion__"
_PROMO_PAGE = "/api/promotions/?page_size=100"
_SETTLE_PAGE = "/api/settlements/?page_size=100"


async def _call(session: AsyncSession, user: Any, method: str, path: str) -> Any:
    """真实路由 + 用户的有效权限（照 test_promotion_amount_log_ui._call）。"""
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
            return await c.request(method, path)
    finally:
        for dep in (get_session, get_current_user_active, get_current_perms):
            app.dependency_overrides.pop(dep, None)


async def _actions(session: AsyncSession, user: Any, path: str) -> dict[str, Any]:
    resp = await _call(session, user, "GET", path)
    assert resp.status_code == 200, resp.text
    actions: dict[str, Any] = resp.json()["ui"]["actions"]
    return actions


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Any:
    token = tenant_id_ctx.set(tenant_a.id)
    yield
    tenant_id_ctx.reset(token)


@pytest.mark.usefixtures("tenant_ctx")
class TestPromotionPageUi:
    @pytest.mark.parametrize(
        ("who", "expected"),
        [("admin", True), ("pr_manager", True), ("pr", False), ("operations", False)],
    )
    async def test_create_import_by_role(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        who: str,
        expected: bool,
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        await promotion_factory.promotion(style=style, blogger=blogger, pr=flow_users.pr)
        user = getattr(flow_users, who)
        actions = await _actions(session, user, _PROMO_PAGE)
        assert ("create" in actions) is expected
        assert ("import" in actions) is expected
        if expected:
            assert actions["create"] == {"state": "enabled"}
            assert actions["import"] == {"state": "enabled"}
        # 与建单接口同一判定：没有标记的，POST 被路由层 403（请求体都还没校验）
        resp = await _call(session, user, "POST", "/api/promotions/")
        if expected:
            assert resp.status_code == 422, resp.text
        else:
            assert resp.status_code == 403, resp.text
            assert resp.json()["code"] == "PERMISSION_DENIED"

    async def test_personal_grant_to_pr(
        self, session: AsyncSession, tenant_a: Any, flow_users: Any
    ) -> None:
        """个人授予 PR ``promotion_create:write`` → 两个标记都有（临时代理建单）。"""
        perm = (
            await session.execute(
                select(Permission).where(Permission.scope == "promotion_create:write")
            )
        ).scalar_one()
        session.add(
            UserPermissionOverride(
                tenant_id=tenant_a.id,
                user_id=flow_users.pr.id,
                permission_id=perm.id,
                effect="grant",
            )
        )
        await session.flush()
        actions = await _actions(session, flow_users.pr, _PROMO_NO_ROWS)
        assert actions["create"] == {"state": "enabled"}
        assert actions["import"] == {"state": "enabled"}
        # 另一个 PR 不受影响
        other = await _actions(session, flow_users.pr2, _PROMO_NO_ROWS)
        assert "create" not in other and "import" not in other

    @pytest.mark.parametrize(("who", "expected"), [("pr_manager", True), ("pr", False)])
    async def test_flags_do_not_depend_on_rows(
        self, session: AsyncSession, flow_users: Any, who: str, expected: bool
    ) -> None:
        resp = await _call(session, getattr(flow_users, who), "GET", _PROMO_NO_ROWS)
        assert resp.status_code == 200, resp.text
        assert resp.json()["items"] == []
        actions = resp.json()["ui"]["actions"]
        assert ("create" in actions) is expected
        assert ("import" in actions) is expected


@pytest.mark.usefixtures("tenant_ctx")
class TestSettlementPageUi:
    @pytest.mark.parametrize(
        ("who", "expected"), [("finance", True), ("admin", True), ("pr_manager", False)]
    )
    async def test_import_by_role(
        self, session: AsyncSession, flow_users: Any, who: str, expected: bool
    ) -> None:
        resp = await _call(session, getattr(flow_users, who), "GET", _SETTLE_PAGE)
        assert resp.status_code == 200, resp.text
        # 0 行照样给
        assert resp.json()["items"] == []
        actions = resp.json()["ui"]["actions"]
        assert ("import" in actions) is expected
        if expected:
            assert actions["import"] == {"state": "enabled"}
