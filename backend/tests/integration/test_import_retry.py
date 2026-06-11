"""U06a 集成测试：retry（NF-3 原子 claim 互斥 + FB-E 两类分流 + retry_count 上限）。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.importer.exceptions import (
    ImportBatchBusyError,
    ImportBatchNotFoundError,
    ImportRetryExhaustedError,
)
from app.modules.importer.repository import ImportBatchRepository
from app.modules.importer.service import ImportService


@pytest.fixture(autouse=True)
def _intercept_celery(monkeypatch):
    """拦截 run_import_batch.apply_async（记录 only_failed/countdown）。"""
    import app.tasks.import_tasks as tasks

    calls: list[dict] = []

    def _fake_apply_async(*, args, kwargs, countdown):
        calls.append({"args": args, "kwargs": kwargs, "countdown": countdown})

    monkeypatch.setattr(tasks.run_import_batch, "apply_async", _fake_apply_async)
    return calls


@pytest.mark.integration
@pytest.mark.asyncio
class TestRetry:
    async def test_retry_partial_uses_only_failed(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        import_batch_factory: Any,
        _intercept_celery: list,
    ) -> None:
        """partial（有 failed 行）→ only_failed=True。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            batch = await import_batch_factory.batch(
                status="partial", failed=2, imported=3, total_rows=5
            )
            svc = ImportService(session)
            claimed = await svc.retry(batch.id, user)
            assert claimed.status == "processing"
            assert claimed.retry_count == 1
            assert _intercept_celery[0]["kwargs"]["only_failed"] is True
        finally:
            tenant_id_ctx.reset(token)

    async def test_retry_failed_uses_whole_file(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        import_batch_factory: Any,
        _intercept_celery: list,
    ) -> None:
        """failed（解析失败，无 failed 行）→ only_failed=False。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            batch = await import_batch_factory.batch(
                status="failed", failed=0, imported=0, total_rows=0
            )
            svc = ImportService(session)
            await svc.retry(batch.id, user)
            assert _intercept_celery[0]["kwargs"]["only_failed"] is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_retry_exhausted_409(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        import_batch_factory: Any,
    ) -> None:
        """retry_count >= 3 → 409 RetryExhausted。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            batch = await import_batch_factory.batch(
                status="failed", retry_count=3
            )
            svc = ImportService(session)
            with pytest.raises(ImportRetryExhaustedError):
                await svc.retry(batch.id, user)
        finally:
            tenant_id_ctx.reset(token)

    async def test_retry_processing_busy_409(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        import_batch_factory: Any,
    ) -> None:
        """processing 状态（正在跑）→ 409 BatchBusy（claim 返回 None）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            batch = await import_batch_factory.batch(status="processing")
            svc = ImportService(session)
            with pytest.raises(ImportBatchBusyError):
                await svc.retry(batch.id, user)
        finally:
            tenant_id_ctx.reset(token)

    async def test_retry_not_found_404(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any
    ) -> None:
        from uuid import uuid4

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_role])
            svc = ImportService(session)
            with pytest.raises(ImportBatchNotFoundError):
                await svc.retry(uuid4(), user)
        finally:
            tenant_id_ctx.reset(token)

    async def test_claim_for_retry_atomic_single_winner(
        self,
        session: AsyncSession,
        tenant_a: Any,
        import_batch_factory: Any,
    ) -> None:
        """NF-3：claim_for_retry 第一次成功置 processing，第二次（已 processing）返回 None。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            batch = await import_batch_factory.batch(status="failed", failed=1)
            repo = ImportBatchRepository(session)
            first = await repo.claim_for_retry(batch.id, tenant_a.id)
            assert first is not None
            assert first.status == "processing"
            second = await repo.claim_for_retry(batch.id, tenant_a.id)
            assert second is None  # 已被领取，互斥
        finally:
            tenant_id_ctx.reset(token)
