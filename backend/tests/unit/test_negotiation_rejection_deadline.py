"""七天边界和真实定时任务注册，不连接数据库。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.celery_app import celery_app
from app.core.tenancy import actor_type_ctx, bypass_rls_ctx, tenant_id_ctx
from app.modules.negotiation.rejection_deadline import rejection_expired
from app.tasks.negotiation_tasks import cancel_rejected_negotiations


def test_exact_168_hour_boundary_and_edit_does_not_extend() -> None:
    rejected = datetime(2026, 11, 16, 2, tzinfo=UTC)
    boundary = rejected + timedelta(days=7)
    edited = rejected + timedelta(days=6)
    assert not rejection_expired(rejected, edited, boundary - timedelta(microseconds=1))
    assert rejection_expired(rejected, edited, boundary)
    assert rejection_expired(rejected, edited, boundary + timedelta(seconds=1))


def test_legacy_updated_at_is_used_only_without_reviewed_at() -> None:
    anchor = datetime(2026, 11, 16, 2, tzinfo=UTC)
    assert not rejection_expired(None, anchor, anchor + timedelta(days=6))
    assert rejection_expired(None, anchor, anchor + timedelta(days=7))


def test_expiry_task_registered_every_five_minutes() -> None:
    entry = celery_app.conf.beat_schedule["cancel-rejected-negotiations"]
    assert entry["task"] == cancel_rejected_negotiations.name
    assert entry["schedule"].minute == set(range(0, 60, 5))
    assert entry["options"]["queue"] == cancel_rejected_negotiations.queue == "default"


@pytest.mark.asyncio
async def test_worker_writes_in_app_tenant_context_without_bypass(monkeypatch):
    from app.tasks import negotiation_tasks as task

    tid = uuid4()
    metadata = AsyncMock()
    metadata.execute.return_value = SimpleNamespace(scalars=lambda: [tid])
    app_session = AsyncMock()
    app_factory, metadata_factory = MagicMock(), MagicMock()
    app_factory.return_value.__aenter__.return_value = app_session
    metadata_factory.return_value.__aenter__.return_value = metadata
    monkeypatch.setattr(task, "AsyncSessionApp", app_factory)
    monkeypatch.setattr(task, "AsyncSessionBypass", metadata_factory)
    before = tenant_id_ctx.get()

    async def capture(session, tenant_id, now):
        assert session is app_session
        assert tenant_id == tenant_id_ctx.get() == tid
        assert not bypass_rls_ctx.get()
        assert actor_type_ctx.get() == "system"
        return 1

    monkeypatch.setattr(task, "cancel_expired_rejections", capture)
    assert await task._cancel_all() == {"tenants": 1, "cancelled": 1, "failed": 0}
    app_session.commit.assert_awaited_once()
    assert tenant_id_ctx.get() == before
