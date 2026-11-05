"""标读必须跨请求持久化；每个请求使用独立连接且关闭时不隐式提交。"""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import get_session
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.deps import get_current_perms, get_current_user_active
from app.modules.auth.service import AuthService
from app.modules.wecom.models import Notification
from app.modules.wecom.notification_api import router
from tests.integration.test_promotion_deactivate_concurrency import (  # noqa: F401
    session,
    tenant_a,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("notice_type", ["follow_up_summary", "urge_manual"])
async def test_notification_read_survives_independent_requests(
    engine,
    session,  # noqa: F811
    flow_users,
    notice_type,
):
    user = flow_users.pr
    tenant_id, user_id = user.tenant_id, user.id
    token = tenant_id_ctx.set(tenant_id)
    try:
        notification = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            type=notice_type,
            content="持久化测试",
            summary_date=datetime.now(UTC).date() if notice_type == "follow_up_summary" else None,
        )
        session.add(notification)
        await session.flush()
        notification_id = notification.id
        permissions = await AuthService(session).load_effective_permissions(user_id)
        await session.commit()
        maker = async_sessionmaker(engine, expire_on_commit=False)
        request_pids = []

        async def independent_session():
            async with maker() as request_session:
                request_pids.append(await request_session.scalar(text("SELECT pg_backend_pid()")))
                yield request_session
                # 故意没有commit：只依赖实际HTTP标读路由提交，关闭会话会回滚遗漏的写入。

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_session] = independent_session
        app.dependency_overrides[get_current_user_active] = lambda: user
        app.dependency_overrides[get_current_perms] = lambda: permissions
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            saved = await client.post(f"/api/notifications/{notification_id}/read")
            assert saved.status_code == 200 and saved.json() == {"ok": True}
            count = await client.get("/api/notifications/unread-count")
            assert count.status_code == 200 and count.json() == {"count": 0}
            listed = await client.get("/api/notifications")
            assert listed.status_code == 200
            assert listed.json()[0]["is_read"] is True
        assert len(request_pids) == 3 and len(set(request_pids)) == 3
        async with maker() as verify:
            assert (
                await verify.scalar(
                    select(Notification.is_read).where(Notification.id == notification_id)
                )
                is True
            )
    finally:
        tenant_id_ctx.reset(token)
