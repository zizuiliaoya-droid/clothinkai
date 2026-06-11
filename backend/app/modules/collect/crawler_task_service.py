"""U13 CrawlerTaskService（调度 + poll SKIP LOCKED + exchange 一次性 + result→import）。

按 P-U13-02/03：
- schedule_for_tenant：逐 active 凭据 INSERT pending（UNIQUE 幂等）
- poll_next_task：FOR UPDATE SKIP LOCKED 原子领取 + 生成一次性 cred_token
- exchange_credential：校验 token+TTL → CredentialService.decrypt → 清空 token
- report_result：success → upload_for_crawler + report_success；failed → report_failure
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import crawler_poll_total, crawler_task_total
from app.modules.collect.config import CRED_TOKEN_TTL_SECONDS
from app.modules.collect.enums import PLATFORM_SOURCE, CrawlerStatus
from app.modules.collect.exceptions import CrawlerTaskNotFound, CredTokenInvalid
from app.modules.collect.models import CrawlerTask, WorkerToken
from app.modules.collect.repository import CrawlerTaskRepository
from app.modules.collect.schemas import (
    CrawlerTaskAssignment,
    CredExchangeResponse,
)
from app.modules.credential.models import Credential
from app.modules.credential.service import CredentialService
from app.modules.importer.service import ImportService

log = logging.getLogger(__name__)


class CrawlerTaskService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = CrawlerTaskRepository(session)
        self._audit = AuditService(session)
        self._cred = CredentialService(session)

    # ------------------------------------------------------------------ #
    # schedule（Beat 逐租户调用）
    # ------------------------------------------------------------------ #
    async def schedule_for_tenant(
        self, tenant_id: UUID, target_date: datetime | None = None
    ) -> int:
        """为单租户全部 active 凭据生成 pending 任务（UNIQUE 幂等）。调用方负责 commit。"""
        td = (target_date or (datetime.now(UTC) - timedelta(days=1))).date()
        creds = (
            await self._session.execute(
                select(Credential).where(
                    Credential.tenant_id == tenant_id,
                    Credential.status == "active",
                )
            )
        ).scalars().all()
        created = 0
        for c in creds:
            await self._session.execute(
                text(
                    "INSERT INTO crawler_task "
                    "(id, tenant_id, platform, credential_id, target_date, status, "
                    " attempt, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :t, :p, :c, :d, 'pending', 0, NOW(), NOW()) "
                    "ON CONFLICT (tenant_id, platform, credential_id, target_date) "
                    "DO NOTHING"
                ),
                {"t": str(tenant_id), "p": c.platform, "c": str(c.id), "d": td},
            )
            created += 1
        return created

    # ------------------------------------------------------------------ #
    # poll（FOR UPDATE SKIP LOCKED 原子领取）
    # ------------------------------------------------------------------ #
    async def poll_next_task(
        self, wt: WorkerToken
    ) -> CrawlerTaskAssignment | None:
        cred_token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(seconds=CRED_TOKEN_TTL_SECONDS)
        row = (
            await self._session.execute(
                text(
                    "UPDATE crawler_task SET status='assigned', worker_token_id=:wt, "
                    "assigned_at=NOW(), cred_token=:ct, cred_token_expires_at=:exp, "
                    "updated_at=NOW() "
                    "WHERE id = (SELECT id FROM crawler_task "
                    "  WHERE tenant_id=:t AND status='pending' "
                    "  ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
                    "RETURNING id, platform, credential_id, target_date"
                ),
                {
                    "wt": str(wt.id),
                    "ct": cred_token,
                    "exp": expires,
                    "t": str(wt.tenant_id),
                },
            )
        ).first()
        if row is None:
            await self._session.commit()
            crawler_poll_total.labels("empty").inc()
            return None
        await self._audit.log(
            action="crawler.poll",
            resource="crawler_task",
            resource_id=row.id,
            after={"worker_token_id": str(wt.id)},
        )
        await self._session.commit()
        crawler_poll_total.labels("assigned").inc()
        return CrawlerTaskAssignment(
            task_id=row.id,
            platform=row.platform,
            credential_id=row.credential_id,
            target_date=row.target_date,
            cred_token=cred_token,
            expires_at=expires,
        )

    # ------------------------------------------------------------------ #
    # exchange（一次性 cred_token + TTL）
    # ------------------------------------------------------------------ #
    async def exchange_credential(
        self, task_id: UUID, cred_token: str
    ) -> CredExchangeResponse:
        task = await self._repo.get(task_id)
        if task is None:
            raise CrawlerTaskNotFound()
        now = datetime.now(UTC)
        if (
            task.status != CrawlerStatus.ASSIGNED.value
            or not task.cred_token
            or task.cred_token != cred_token
            or task.cred_token_expires_at is None
            or task.cred_token_expires_at < now
        ):
            raise CredTokenInvalid()
        plaintext = await self._cred.decrypt_for_purpose(
            task.credential_id, purpose=f"crawler_{task.platform}"
        )
        # 一次性清空 + 推进状态
        task.cred_token = None
        task.status = CrawlerStatus.EXCHANGED.value
        await self._session.flush()
        await self._audit.log(
            action="crawler.exchange",
            resource="crawler_task",
            resource_id=task.id,
        )
        await self._session.commit()
        cred = await self._session.get(Credential, task.credential_id)
        username = cred.username if cred else ""
        return CredExchangeResponse(username=username, password=plaintext)

    # ------------------------------------------------------------------ #
    # report_result（success → import；failed → report_failure）
    # ------------------------------------------------------------------ #
    async def report_result(
        self,
        task_id: UUID,
        status: str,
        *,
        content: bytes | None = None,
        filename: str | None = None,
        error: str | None = None,
    ) -> dict:
        task = await self._repo.get(task_id)
        if task is None:
            raise CrawlerTaskNotFound()
        if status == "success":
            source = PLATFORM_SOURCE.get(task.platform, task.platform)
            batch = await ImportService(self._session).upload_for_crawler(
                content=content or b"",
                source=source,
                tenant_id=task.tenant_id,
                filename=filename or f"{source}_{task.target_date}.csv",
            )
            task.import_batch_id = batch.id
            task.status = CrawlerStatus.SUCCESS.value
            await self._cred.report_success(task.credential_id)
            crawler_task_total.labels(task.platform, "success").inc()
        else:
            task.error_reason = error
            task.status = CrawlerStatus.FAILED.value
            await self._cred.report_failure(
                task.credential_id, error or "采集失败"
            )
            crawler_task_total.labels(task.platform, "failed").inc()
        await self._session.flush()
        await self._audit.log(
            action="crawler.result",
            resource="crawler_task",
            resource_id=task.id,
            after={"status": status},
        )
        await self._session.commit()
        return {
            "ok": True,
            "batch_id": str(task.import_batch_id)
            if task.import_batch_id
            else None,
        }


__all__ = ["CrawlerTaskService"]
