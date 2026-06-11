"""备份恢复演练脚本（NFR04 + Q10=B 半自动）。

使用方式（在 staging 环境运行）：

    python -m backend.scripts.restore_backup --backup-id <UUID>
    python -m backend.scripts.restore_backup --date 2026-05-23

流程：
    1. 从 backup_record 加载备份信息（或按日期查找）
    2. 从 R2 下载 daily-{date}.tar.gz
    3. 校验 SHA256
    4. 解压 → 取出 pg dump
    5. pg_restore 到目标 DB
    6. 跑 smoke test（用户能登录 + audit_log 可查 + 默认 tenant 存在）
    7. 写 backup_record(type=restore_drill)

每季度运行一次，输出验收清单（Markdown）供运维归档。
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import logging
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.attachment import attachment_service
from app.core.config import settings
from app.modules.auth.models import BackupRecord

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("restore_backup")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def restore_main(
    *,
    backup_id: UUID | None,
    backup_date: date | None,
    target_db_url: str,
) -> int:
    """返回 exit code（0=成功，非 0=失败）。"""
    if not backup_id and not backup_date:
        log.error("必须指定 --backup-id 或 --date")
        return 2

    # 1. 加载 backup_record
    async with _bypass_engine() as engine:
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            record = await _load_backup_record(session, backup_id, backup_date)
            if record is None:
                log.error("找不到备份记录")
                return 2
            log.info(
                "loaded_backup",
                extra={"backup_id": str(record.id), "r2_key": record.r2_key},
            )

    if not record.r2_key:
        log.error("备份记录缺少 r2_key")
        return 2

    # 2. 下载 + 校验 + 恢复
    drill_id = uuid4()
    drill_started = datetime.now(timezone.utc)
    drill_result: dict[str, str] = {"status": "running"}

    try:
        with tempfile.TemporaryDirectory(prefix="cerp-restore-") as tmpdir:
            tmp = Path(tmpdir)
            archive = tmp / Path(record.r2_key).name

            # 2a. 下载
            log.info("downloading", extra={"r2_key": record.r2_key})
            _download_from_r2(record.r2_key, archive)

            # 2b. SHA256 校验
            if record.checksum:
                actual = _sha256(archive)
                if actual != record.checksum:
                    raise ValueError(f"checksum mismatch: {actual} != {record.checksum}")
                log.info("checksum_ok")

            # 2c. 解压
            with tarfile.open(archive, "r:gz") as tar:
                tar.extractall(tmp)
            pg_dump_files = list(tmp.glob("pg-*.sql.gz"))
            if not pg_dump_files:
                raise FileNotFoundError("解压后未找到 pg-*.sql.gz")
            pg_dump = pg_dump_files[0]

            # 2d. 解压 sql.gz → sql
            sql_file = tmp / pg_dump.name.replace(".gz", "")
            with gzip.open(pg_dump, "rb") as fin, sql_file.open("wb") as fout:
                shutil.copyfileobj(fin, fout)

            # 2e. psql 恢复
            log.info("restoring", extra={"target": target_db_url})
            _run_psql_restore(target_db_url, sql_file)

        # 3. Smoke test
        log.info("smoke_test_starting")
        smoke_results = await _smoke_test(target_db_url)
        log.info("smoke_test_results", extra=smoke_results)
        if not all(smoke_results.values()):
            raise RuntimeError(f"smoke test failed: {smoke_results}")

        drill_result["status"] = "success"
    except Exception as exc:
        drill_result["status"] = "failed"
        drill_result["error"] = str(exc)
        log.exception("restore_drill_failed")

    # 4. 记录演练结果（写入主 DB，不是 staging）
    async with _bypass_engine() as engine:
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as session:
            session.add(
                BackupRecord(
                    id=drill_id,
                    backup_type="restore_drill",
                    started_at=drill_started,
                    completed_at=datetime.now(timezone.utc),
                    status=drill_result["status"],
                    r2_key=record.r2_key,
                    error_message=drill_result.get("error"),
                    includes={"source_backup_id": str(record.id)},
                )
            )
            await session.commit()

    # 5. 输出验收清单
    _print_acceptance_checklist(drill_id, record, drill_result)

    return 0 if drill_result["status"] == "success" else 1


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _bypass_engine() -> object:
    """临时引擎（仅本脚本用）。返回 async context manager-like 对象。"""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        engine = create_async_engine(settings.DATABASE_URL_BYPASS, future=True)
        try:
            yield engine
        finally:
            await engine.dispose()

    return _ctx()


async def _load_backup_record(
    session: object,
    backup_id: UUID | None,
    backup_date: date | None,
) -> BackupRecord | None:
    if backup_id:
        return await session.get(BackupRecord, backup_id)  # type: ignore[attr-defined]
    if backup_date:
        from sqlalchemy import and_

        stmt = (
            select(BackupRecord)
            .where(
                and_(
                    BackupRecord.status == "success",
                    BackupRecord.backup_type.in_(["daily", "monthly"]),
                )
            )
            .order_by(BackupRecord.completed_at.desc())
        )
        records = (await session.execute(stmt)).scalars().all()  # type: ignore[attr-defined]
        for r in records:
            if r.completed_at and r.completed_at.date() == backup_date:
                return r
    return None


def _download_from_r2(r2_key: str, target: Path) -> None:
    """从 R2 backups 桶下载到本地。"""
    if not attachment_service.is_configured:
        raise RuntimeError("R2 未配置")
    client = attachment_service._client  # type: ignore[attr-defined]
    bucket = settings.R2_BUCKET_BACKUPS
    client.download_file(bucket, r2_key, str(target))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _run_psql_restore(target_db_url: str, sql_file: Path) -> None:
    cmd = ["psql", "--dbname", target_db_url, "--file", str(sql_file)]
    result = subprocess.run(cmd, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"psql restore failed (rc={result.returncode}): "
            f"{result.stderr.decode('utf-8', errors='ignore')[:500]}"
        )


async def _smoke_test(target_db_url: str) -> dict[str, bool]:
    """连接到恢复后的 DB，验证关键数据。"""
    from sqlalchemy import text

    engine = create_async_engine(target_db_url, future=True)
    results: dict[str, bool] = {}
    try:
        async with engine.connect() as conn:
            # 1. default tenant 存在
            row = await conn.execute(
                text("SELECT count(*) FROM tenant WHERE code = 'default'")
            )
            results["has_default_tenant"] = row.scalar_one() >= 1

            # 2. 至少一个 admin 用户
            row = await conn.execute(
                text(
                    """
SELECT count(*) FROM "user" u
JOIN user_role ur ON ur.user_id = u.id
JOIN role r ON r.id = ur.role_id
WHERE r.code = 'admin' AND u.deleted_at IS NULL
"""
                )
            )
            results["has_admin_user"] = row.scalar_one() >= 1

            # 3. role 表 = 10 条
            row = await conn.execute(text("SELECT count(*) FROM role WHERE is_system = true"))
            results["has_10_system_roles"] = row.scalar_one() >= 10

            # 4. audit_log 至少能查询（不一定有数据）
            row = await conn.execute(text("SELECT count(*) FROM audit_log"))
            results["audit_log_queryable"] = True
    finally:
        await engine.dispose()
    return results


def _print_acceptance_checklist(
    drill_id: UUID,
    source: BackupRecord,
    drill_result: dict[str, str],
) -> None:
    """输出 Markdown 验收清单。"""
    print("\n" + "=" * 70)
    print(f"# 备份恢复演练验收清单 ({drill_id})\n")
    print(f"- **演练 ID**: {drill_id}")
    print(f"- **源备份**: {source.id} ({source.backup_type})")
    print(f"- **R2 Key**: {source.r2_key}")
    print(f"- **演练状态**: {drill_result['status']}")
    if drill_result.get("error"):
        print(f"- **错误**: {drill_result['error']}")
    print("\n## Checklist")
    print("- [ ] 演练记录已写入 backup_record（status=restore_drill）")
    print("- [ ] Smoke test 全部通过")
    print("- [ ] 已验证恢复后用户可登录")
    print("- [ ] 已记录到运维归档（季度报告）")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup restore drill (NFR04)")
    parser.add_argument("--backup-id", type=str, help="按 backup_record.id 恢复")
    parser.add_argument("--date", type=str, help="按日期 YYYY-MM-DD 恢复")
    parser.add_argument(
        "--target",
        type=str,
        required=True,
        help="目标 DB URL（建议为 staging，避免覆盖生产）",
    )
    args = parser.parse_args()

    backup_id = UUID(args.backup_id) if args.backup_id else None
    backup_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else None
    )

    return asyncio.run(
        restore_main(
            backup_id=backup_id,
            backup_date=backup_date,
            target_db_url=args.target,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
