"""备份任务（NFR04）。

业务规则：
- 每日 03:00 触发 backup_database
- 每日 04:00 触发 cleanup_expired_backups
- pg_dump → R2 backups/daily/{YYYY-MM-DD}/daily-{YYYY-MM-DD}.tar.gz
- 30 天每日 + 1 年每月保留策略
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import shutil
import subprocess
import tarfile
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import sentry_sdk
from celery import Task
from sqlalchemy import delete, select

from app.core.attachment import attachment_service
from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.db import AsyncSessionBypass
from app.modules.auth.models import BackupRecord

log = logging.getLogger(__name__)

DAILY_PREFIX = "daily"
MONTHLY_PREFIX = "monthly"


# ---------------------------------------------------------------------------
# 主备份任务
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.backup_tasks.backup_database",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2, "countdown": 300},
    queue="backup",
)
def backup_database(self: Task) -> dict[str, Any]:
    """每日全库备份。

    步骤（详见 functional-design 7.1）：
    1. 创建 backup_record(running)
    2. pg_dump → /tmp
    3. 凭据桶元数据导出 → /tmp
    4. 关键配置导出 → /tmp
    5. 合并 + gzip + SHA256
    6. 上传 R2
    7. backup_record.status = success
    8. 失败：retry 2 次后 capture_exception
    """
    return asyncio.run(_run_backup_database(self))


async def _run_backup_database(task: Task) -> dict[str, Any]:
    today = date.today()
    is_first_of_month = today.day == 1
    backup_type = "monthly" if is_first_of_month else "daily"
    started_at = datetime.now(timezone.utc)

    record_id = None
    out_path: Path | None = None
    try:
        async with AsyncSessionBypass() as session:
            record = BackupRecord(
                backup_type=backup_type,
                started_at=started_at,
                status="running",
                includes={"pg_dump": True, "config": True},
            )
            session.add(record)
            await session.commit()
            record_id = record.id

        # 1. pg_dump
        with tempfile.TemporaryDirectory(prefix="cerp-backup-") as tmpdir:
            tmp = Path(tmpdir)
            pg_path = tmp / f"pg-{today.isoformat()}.sql.gz"
            _run_pg_dump(pg_path)

            # 2. 配置导出（暂时占位，U01 仅 role/permission seed 是不变的）
            config_path = tmp / f"config-{today.isoformat()}.json.gz"
            _export_config(config_path)

            # 3. 合并到 tar.gz
            out_path = tmp / f"{backup_type}-{today.isoformat()}.tar.gz"
            with tarfile.open(out_path, "w:gz") as tar:
                tar.add(pg_path, arcname=pg_path.name)
                tar.add(config_path, arcname=config_path.name)

            # 4. SHA256
            checksum = _sha256_of_file(out_path)
            size_bytes = out_path.stat().st_size

            # 5. 上传 R2
            r2_key = f"{backup_type}/{today.isoformat()}/{out_path.name}"
            with out_path.open("rb") as f:
                attachment_service.upload_bytes(
                    f, bucket="backups", key=r2_key, content_type="application/gzip"
                )

        # 6. 更新 backup_record
        retention_until = (
            today + timedelta(days=settings.BACKUP_RETAIN_MONTHLY_MONTHS * 30)
            if backup_type == "monthly"
            else today + timedelta(days=settings.BACKUP_RETAIN_DAILY_DAYS)
        )
        async with AsyncSessionBypass() as session:
            record = await session.get(BackupRecord, record_id)
            if record is not None:
                record.completed_at = datetime.now(timezone.utc)
                record.status = "success"
                record.r2_key = r2_key
                record.size_bytes = size_bytes
                record.checksum = checksum
                record.retention_until = retention_until
                await session.commit()

        log.info(
            "backup_completed",
            extra={
                "backup_type": backup_type,
                "r2_key": r2_key,
                "size_bytes": size_bytes,
            },
        )
        return {"status": "success", "r2_key": r2_key, "size_bytes": size_bytes}

    except Exception as exc:  # noqa: BLE001
        if task.request.retries >= task.max_retries:
            # 最后一次失败：写 record + Sentry
            async with AsyncSessionBypass() as session:
                if record_id is not None:
                    record = await session.get(BackupRecord, record_id)
                    if record is not None:
                        record.status = "failed"
                        record.error_message = str(exc)[:2000]
                        record.completed_at = datetime.now(timezone.utc)
                        await session.commit()
            sentry_sdk.capture_exception(exc)
            log.exception("backup_failed_terminal")
        raise


def _run_pg_dump(out_path: Path) -> None:
    """通过 subprocess 调用 pg_dump 输出 gzip 归档。"""
    import os

    cmd = [
        "pg_dump",
        "--format=plain",
        "--no-owner",
        "--no-acl",
        "--dbname",
        settings.DATABASE_URL_SYNC,
    ]
    with gzip.open(out_path, "wb") as fout:
        result = subprocess.run(
            cmd,
            stdout=fout,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"pg_dump 失败 (returncode={result.returncode}): {result.stderr.decode('utf-8', errors='ignore')[:500]}"
        )


def _export_config(out_path: Path) -> None:
    """导出关键配置到 gzip JSON（U01 阶段先占位空 dict）。"""
    payload: dict[str, Any] = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
        # 后续单元会在此处加入：field_mapping / message_template / role_permission
    }
    with gzip.open(out_path, "wb") as f:
        f.write(json.dumps(payload).encode("utf-8"))


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 清理过期备份
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="app.tasks.backup_tasks.cleanup_expired_backups",
    queue="backup",
)
def cleanup_expired_backups(self: Task) -> dict[str, Any]:
    return asyncio.run(_run_cleanup_expired_backups())


async def _run_cleanup_expired_backups() -> dict[str, Any]:
    today = date.today()
    deleted_count = 0
    async with AsyncSessionBypass() as session:
        stmt = select(BackupRecord).where(
            BackupRecord.retention_until.isnot(None),
            BackupRecord.retention_until < today,
            BackupRecord.r2_key.isnot(None),
        )
        records = (await session.execute(stmt)).scalars().all()
        for record in records:
            try:
                if record.r2_key:
                    attachment_service.delete("backups", record.r2_key)
                await session.delete(record)
                deleted_count += 1
            except Exception:  # noqa: BLE001
                log.exception(
                    "cleanup_backup_failed",
                    extra={"backup_id": str(record.id), "r2_key": record.r2_key},
                )
        await session.commit()
    log.info("cleanup_backups_done", extra={"deleted": deleted_count})
    return {"deleted": deleted_count}
