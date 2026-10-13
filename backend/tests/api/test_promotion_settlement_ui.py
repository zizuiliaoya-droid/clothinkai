"""推广单矩阵快照带结款单状态（流程线 PR-6 S3，5.3 ⁹ / 计划 D9）。

收款码那一格在「待财务付款」里按结款单状态拆：结款单待付款（财务还没填实付）PR 能改，
结款单待财务付款（财务已填实付）锁成读，管理员不受限。列表整页一次批量取结款单状态
（``get_payment_attachment_refs``），详情单条现查，两条路径给同一个结果。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx

pytestmark = [pytest.mark.api, pytest.mark.asyncio]


async def _call(session: AsyncSession, user: Any, path: str) -> Any:
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
class TestPaymentQrBySettlementStatus:
    @pytest.mark.parametrize(
        ("settlement_status", "pr_can_edit"),
        [("待付款", True), ("待财务付款", False)],
    )
    async def test_list_and_detail_follow_settlement(
        self,
        session: AsyncSession,
        flow_users: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        settlement_factory: Any,
        settlement_status: str,
        pr_can_edit: bool,
    ) -> None:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        promo = await promotion_factory.promotion(
            style=style,
            blogger=blogger,
            pr=flow_users.pr,
            publish_status="已发布",
            settlement_status="待付款",
            actual_publish_date=date(2026, 5, 20),
        )
        await settlement_factory.settlement(
            style=style,
            blogger=blogger,
            promotion=promo,
            pr=flow_users.pr,
            settlement_status=settlement_status,
        )
        for who, expected in (("pr", pr_can_edit), ("pr_manager", pr_can_edit), ("admin", True)):
            user = getattr(flow_users, who)
            page = await _call(
                session, user, f"/api/promotions/?page_size=20&keyword={promo.internal_code}"
            )
            assert page.status_code == 200, page.text
            [row] = [r for r in page.json()["items"] if r["id"] == str(promo.id)]
            assert row["ui"]["column"] == "E", who
            assert ("payment_qr" in row["ui"]["edits"]) is expected, who

            detail = await _call(session, user, f"/api/promotions/{promo.id}")
            assert detail.status_code == 200, detail.text
            fields = detail.json()["ui"]["fields"]
            assert fields["payment_qr"] == {"state": "edit" if expected else "read"}, who
