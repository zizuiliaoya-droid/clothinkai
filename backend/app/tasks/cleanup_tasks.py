"""清理任务（过期 refresh_token、audit_log 归档）。"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import sentry_sdk
from celery import Task
from sqlalchemy import delete, select

from app.core.attachment import attachment_service
from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.db import AsyncSessionBypass
from app.modules.auth.models import AuditLog, RefreshToken

log = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.cleanup_tasks.cleanup_expired_refresh_tokens",
    queue="default",
)
def cleanup_expired_refresh_tokens() -> dict[str, Any]:
    """删除已过期的 refresh_token（每日 04:30）。"""
    return asyncio.run(_run_cleanup_refresh_tokens())


async def _run_cleanup_refresh_tokens() -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    async with AsyncSessionBypass() as session:
        result = await session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < now)
        )
        await session.commit()
        deleted = int(result.rowcount or 0)
    log.info("cleanup_refresh_tokens_done", extra={"deleted": deleted})
    return {"deleted": deleted}


@celery_app.task(
    name="app.tasks.cleanup_tasks.archive_audit_logs",
    queue="backup",
)
def archive_audit_logs() -> dict[str, Any]:
    """归档超过 AUDIT_RETAIN_MONTHS 的 audit_log 到 R2，再从 DB 删除（每月 1 日 04:30）。"""
    return asyncio.run(_run_archive_audit_logs())


async def _run_archive_audit_logs() -> dict[str, Any]:
    threshold = datetime.now(timezone.utc) - timedelta(
        days=settings.AUDIT_RETAIN_MONTHS * 30
    )
    archived_count = 0
    try:
        async with AsyncSessionBypass() as session:
            stmt = select(AuditLog).where(AuditLog.created_at < threshold).limit(10_000)
            records = (await session.execute(stmt)).scalars().all()
            if not records:
                return {"archived": 0}

            with tempfile.TemporaryDirectory(prefix="cerp-audit-arch-") as tmpdir:
                tmp = Path(tmpdir) / f"audit-archive-{threshold.strftime('%Y-%m')}.jsonl.gz"
                with gzip.open(tmp, "wb") as f:
                    for r in records:
                        line = (
                            json.dumps(
                                {
                                    "id": r.id,
                                    "tenant_id": str(r.tenant_id) if r.tenant_id else None,
                                    "user_id": str(r.user_id) if r.user_id else None,
                                    "actor_type": r.actor_type,
                                    "action": r.action,
                                    "resource": r.resource,
                                    "resource_id": r.resource_id,
                                    "before": r.before,
                                    "after": r.after,
                                    "purpose": r.purpose,
                                    "ip": r.ip,
                                    "user_agent": r.user_agent,
                                    "request_id": r.request_id,
                                    "created_at": r.created_at.isoformat(),
                                }
                            )
                            + "\n"
                        )
                        f.write(line.encode("utf-8"))

                # 上传
                tenant_str = "global"  # archive 跨租户分文件可后续优化
                r2_key = (
                    f"audit-archive/{tenant_str}/"
                    f"{threshold.strftime('%Y-%m')}-batch{records[0].id}.jsonl.gz"
                )
                with tmp.open("rb") as fh:
                    attachment_service.upload_bytes(
                        fh, bucket="backups", key=r2_key, content_type="application/gzip"
                    )

            # 删除已归档记录（用 archiver role 实际更安全；U01 用 bypass 引擎兼可）
            ids = [r.id for r in records]
            del_result = await session.execute(
                delete(AuditLog).where(AuditLog.id.in_(ids))
            )
            await session.commit()
            archived_count = int(del_result.rowcount or 0)

        log.info("archive_audit_logs_done", extra={"archived": archived_count})
        return {"archived": archived_count}
    except Exception as exc:  # noqa: BLE001
        sentry_sdk.capture_exception(exc)
        log.exception("archive_audit_logs_failed")
        raise
