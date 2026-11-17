"""真实 clothing_app 下，取消任务不会跨租户修改单据/事件。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select, text

from app.core.tenancy import actor_type_ctx, bypass_rls_ctx, tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.negotiation.rejection_cancel import cancel_expired_rejections
from app.tasks import negotiation_tasks
from tests.integration.test_return_freight_payment_rls import _as_app

pytestmark = [pytest.mark.integration, pytest.mark.rls, pytest.mark.asyncio]


async def test_cancel_expired_uses_real_tenant_rls(
    session,
    tenant_a,
    tenant_b,
    flow_users,
    factory,
    product_factory,
    blogger_factory,
    negotiation_factory,
    monkeypatch,
):
    role = await session.scalar(select(Role).where(Role.code == "pr"))
    token = tenant_id_ctx.set(tenant_b.id)
    try:
        other_pr = await factory.user(tenant_b, roles=[role])
    finally:
        tenant_id_ctx.reset(token)
    anchor = datetime(2026, 11, 16, 2, tzinfo=UTC)
    rows = []
    for tenant, pr in ((tenant_a, flow_users.pr), (tenant_b, other_pr)):
        rows.append(
            await negotiation_factory.negotiation(
                style=await product_factory.style(tenant=tenant),
                blogger=await blogger_factory.blogger(tenant=tenant),
                pr=pr,
                tenant=tenant,
                status="审核驳回",
                reviewed_at=anchor,
                review_opinion="保留原因",
            )
        )
    await _as_app(session, tenant_a.id)
    assert await cancel_expired_rejections(session, tenant_b.id, anchor + timedelta(days=7)) == 0
    # 直接走实际任务入口，租户元数据仅列出 A；业务连接使用真实 clothing_app。
    metadata = AsyncMock()
    metadata.execute.return_value = SimpleNamespace(scalars=lambda: [tenant_a.id])
    metadata_factory = MagicMock()
    metadata_factory.return_value.__aenter__.return_value = metadata

    @asynccontextmanager
    async def app_session():
        assert tenant_id_ctx.get() == tenant_a.id
        assert actor_type_ctx.get() == "system"
        assert not bypass_rls_ctx.get()
        await _as_app(session, tenant_a.id)
        yield session

    class Clock:
        @staticmethod
        def now(tz):
            return anchor + timedelta(days=7)

    monkeypatch.setattr(negotiation_tasks, "AsyncSessionBypass", metadata_factory)
    monkeypatch.setattr(negotiation_tasks, "AsyncSessionApp", app_session)
    monkeypatch.setattr(negotiation_tasks, "datetime", Clock)
    assert await negotiation_tasks._cancel_all() == {"tenants": 1, "cancelled": 1, "failed": 0}
    await _as_app(session, tenant_a.id)
    assert (
        await session.scalars(
            text("SELECT negotiation_id FROM line_event WHERE event_type='negotiation.cancelled'")
        )
    ).all() == [rows[0].id]
    await session.execute(text("RESET ROLE"))
    statuses = dict(
        (
            await session.execute(
                text("SELECT id,status FROM negotiation WHERE id IN (:a,:b)"),
                {"a": rows[0].id, "b": rows[1].id},
            )
        ).all()
    )
    assert statuses == {rows[0].id: "已取消", rows[1].id: "审核驳回"}
