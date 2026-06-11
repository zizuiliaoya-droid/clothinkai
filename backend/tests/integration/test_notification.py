"""U07 集成测试：站内通知（EP08-S07 支撑）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.tenancy import tenant_id_ctx
from app.modules.wecom.notification_service import NotificationService


@pytest.mark.integration
@pytest.mark.asyncio
class TestNotification:
    async def test_notify_and_unread_and_mark_read(
        self, session: Any, tenant_a: Any, factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a)
            svc = NotificationService(session)
            await svc.notify([user.id], "请手动催发 小美", link="/promotions")
            await session.flush()

            assert await svc.unread_count(user_id=user.id) == 1
            rows = await svc.list_for_user(user_id=user.id)
            assert len(rows) == 1
            assert rows[0].content == "请手动催发 小美"

            ok = await svc.mark_read(
                notification_id=rows[0].id, user_id=user.id
            )
            assert ok is True
            assert await svc.unread_count(user_id=user.id) == 0
        finally:
            tenant_id_ctx.reset(tok)

    async def test_mark_read_other_user_denied(
        self, session: Any, tenant_a: Any, factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            user_a = await factory.user(tenant_a)
            user_b = await factory.user(tenant_a)
            svc = NotificationService(session)
            await svc.notify([user_a.id], "私密通知")
            await session.flush()
            rows = await svc.list_for_user(user_id=user_a.id)
            ok = await svc.mark_read(
                notification_id=rows[0].id, user_id=user_b.id
            )
            assert ok is False
        finally:
            tenant_id_ctx.reset(tok)
