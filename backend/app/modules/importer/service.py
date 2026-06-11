"""U06a 导入服务（ImportService）。

按 nfr-design-patterns.md P-U06a-03/04/05 实现：
- ``upload``：**DB 先行 + UNIQUE 原子去重 + R2 失败补偿**（NF-2）/ 三层大小防护 L2（NF-6）
- ``get_batch`` / ``list_batches``：读查询
- ``retry``：**原子 claim 互斥**（NF-3）+ **两类失败分流**（FB-E）
- ``download_errors``：失败明细 CSV（**csv_safe injection 防护**）

关键设计：
- 用 U01 R2 helper（``upload_bytes`` / ``get_object_bytes``），**不碰 Attachment ORM**（FB-A）
- source 白名单校验（``ImportAdapterRegistry.sources()``）
- run_import_batch 懒导入（避免 service ↔ task 循环依赖）
"""

from __future__ import annotations

import csv
import io
import json
import logging
from collections.abc import Sequence
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.core.attachment import BucketKind
from app.core.attachment import attachment_service as _default_attachment_service
from app.core.audit import AuditService
from app.core.config import settings
from app.core.metrics import (
    import_file_size_bytes,
    import_retry_total,
)
from app.modules.auth.models import User
from app.modules.importer.domain import compute_sha256, csv_safe, safe_filename
from app.modules.importer.exceptions import (
    ImportBatchBusyError,
    ImportBatchNotFoundError,
    ImportDuplicateFileError,
    ImportFileTooLargeError,
    ImportFormatUnsupportedError,
    ImportMappingVersionNotFoundError,
    ImportRetryExhaustedError,
    ImportSourceUnknownError,
    ImportStorageError,
)
from app.modules.importer.models import ImportBatch
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.repository import (
    ImportBatchListFilters,
    ImportBatchRepository,
    ImportJobRepository,
)

log = logging.getLogger(__name__)

# 文件格式白名单（扩展名 + MIME，P-U06a-05）
_ALLOWED_EXT = (".csv", ".xlsx")
_ALLOWED_MIME = frozenset(
    {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",  # 部分浏览器对 csv 用此 MIME
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",  # 兜底（以扩展名为准）
    }
)


class ImportService:
    """统一导入服务。"""

    def __init__(self, session, attachment_service=None) -> None:  # type: ignore[no-untyped-def]
        self._session = session
        self._repo = ImportBatchRepository(session)
        self._job_repo = ImportJobRepository(session)
        self._audit = AuditService(session)
        self._attachment = attachment_service or _default_attachment_service

    # ============================================================
    # upload（DB 先行 + UNIQUE 原子去重 + R2 补偿，NF-2）
    # ============================================================

    async def upload(
        self,
        *,
        content: bytes,
        filename: str | None,
        content_type: str | None,
        source: str,
        user: User,
        mapping_version: int | None = None,
    ) -> ImportBatch:
        """上传导入文件，创建 batch（status=processing）并异步触发解析。

        Args:
            content: 已读入内存的文件字节（handler 负责读取，L2 大小兜底在此校验）。
            filename / content_type: 原始文件名与 MIME（格式白名单校验）。
            source: 导入来源（须在 ImportAdapterRegistry 白名单内）。
            mapping_version: 指定字段映射版本；None → 用 active 版本（可为空）。

        Raises:
            ImportSourceUnknownError(422) / ImportFormatUnsupportedError(422) /
            ImportFileTooLargeError(422) / ImportMappingVersionNotFoundError(422) /
            ImportDuplicateFileError(409) / ImportStorageError(500)
        """
        # 0. source 白名单（NF：upload 时拒未注册）
        if source not in ImportAdapterRegistry.sources():
            raise ImportSourceUnknownError(source)

        # 1. 格式白名单（扩展名 + MIME）
        self._assert_format(filename, content_type)

        # 2. L2 大小兜底（NF-6）+ 流式 hash
        size_bytes = len(content)
        if size_bytes > settings.IMPORT_MAX_FILE_MB * 1024 * 1024:
            raise ImportFileTooLargeError()
        file_hash, _ = compute_sha256(io.BytesIO(content))
        import_file_size_bytes.labels(source=source).observe(size_bytes)

        # 捕获主键/租户为本地变量（避免后续 SAVEPOINT 回滚后再触发 ORM 懒加载）
        tenant_id = user.tenant_id
        actor_id = user.id

        # 3. 解析 mapping_version（指定 → 校验存在；None → 用 active）
        resolved_version = await self._resolve_mapping_version(
            source, mapping_version, tenant_id
        )

        # 4. NF-2 DB 先行：INSERT batch，靠 UNIQUE(tenant,source,hash) 原子拦并发
        batch_id = uuid4()
        r2_key = f"imports/{tenant_id}/{batch_id}/{safe_filename(filename)}"
        batch = ImportBatch(
            id=batch_id,
            tenant_id=tenant_id,
            source=source,
            file_hash=file_hash,
            original_filename=safe_filename(filename),
            file_r2_key=r2_key,
            file_bucket=settings.IMPORT_BUCKET,
            mapping_version=resolved_version,
            status="processing",
            created_by=actor_id,
        )
        # NF-2：INSERT + R2 写入同处于一个 SAVEPOINT —— 任一失败仅回滚该 savepoint，
        # 不污染请求事务其余部分；用 SAVEPOINT 而非 session.rollback()（后者会丢弃整个事务）。
        try:
            async with self._session.begin_nested():
                self._repo.add(batch)
                await self._session.flush()  # UNIQUE 冲突在此抛 IntegrityError

                # 5. flush 成功才写 R2（key 含 batch_id，并发不互相覆盖）
                try:
                    self._attachment.upload_bytes(
                        content,
                        bucket=cast(BucketKind, settings.IMPORT_BUCKET),
                        key=r2_key,
                        content_type=content_type or "application/octet-stream",
                    )
                except Exception as exc:  # noqa: BLE001
                    # NF-2 补偿：R2 写失败 → 抛出触发 savepoint 回滚（batch 行被撤销，无孤儿）
                    log.exception(
                        "import_upload_r2_failed",
                        extra={"batch_id": str(batch_id), "source": source},
                    )
                    raise ImportStorageError() from exc
        except IntegrityError:
            existing = await self._repo.find_by_hash(tenant_id, source, file_hash)
            raise ImportDuplicateFileError(
                batch_id=existing.id if existing else None
            )

        await self._audit.log(
            action="import.upload",
            resource="import_batch",
            resource_id=batch_id,
            after={"source": source, "file_hash": file_hash},
            user_id=actor_id,
        )
        await self._session.commit()
        await self._session.refresh(batch)

        # 6. 异步触发（懒导入避免循环依赖）
        from app.tasks.import_tasks import run_import_batch

        run_import_batch.delay(str(batch_id))
        return batch

    # ============================================================
    # upload_for_crawler（U13 系统 actor 入口，无 HTTP User）
    # ============================================================

    async def upload_for_crawler(
        self,
        *,
        content: bytes,
        source: str,
        tenant_id: UUID,
        filename: str,
        content_type: str = "text/csv",
        mapping_version: int | None = None,
    ) -> ImportBatch:
        """采集 Worker result 成功路径：系统 actor 创建 batch + 触发导入。

        与 ``upload`` 共用核心逻辑，但 actor_id=None（audit actor_type=worker），
        tenant_id 由调用方（CrawlerTaskService，system_context）显式传入。
        source 已由 platform 映射，不再校验白名单失败时静默（缺 adapter → batch.failed）。
        """
        if source not in ImportAdapterRegistry.sources():
            raise ImportSourceUnknownError(source)

        size_bytes = len(content)
        if size_bytes > settings.IMPORT_MAX_FILE_MB * 1024 * 1024:
            raise ImportFileTooLargeError()
        file_hash, _ = compute_sha256(io.BytesIO(content))
        import_file_size_bytes.labels(source=source).observe(size_bytes)

        resolved_version = await self._resolve_mapping_version(
            source, mapping_version, tenant_id
        )
        batch_id = uuid4()
        r2_key = f"imports/{tenant_id}/{batch_id}/{safe_filename(filename)}"
        batch = ImportBatch(
            id=batch_id,
            tenant_id=tenant_id,
            source=source,
            file_hash=file_hash,
            original_filename=safe_filename(filename),
            file_r2_key=r2_key,
            file_bucket=settings.IMPORT_BUCKET,
            mapping_version=resolved_version,
            status="processing",
            created_by=None,
        )
        try:
            async with self._session.begin_nested():
                self._repo.add(batch)
                await self._session.flush()
                try:
                    self._attachment.upload_bytes(
                        content,
                        bucket=cast(BucketKind, settings.IMPORT_BUCKET),
                        key=r2_key,
                        content_type=content_type,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "crawler_import_upload_r2_failed",
                        extra={"batch_id": str(batch_id), "source": source},
                    )
                    raise ImportStorageError() from exc
        except IntegrityError:
            existing = await self._repo.find_by_hash(tenant_id, source, file_hash)
            raise ImportDuplicateFileError(
                batch_id=existing.id if existing else None
            )

        await self._audit.log(
            action="import.upload_via_crawler",
            resource="import_batch",
            resource_id=batch_id,
            after={"source": source, "file_hash": file_hash},
            actor_type="worker",
        )
        await self._session.commit()
        await self._session.refresh(batch)

        from app.tasks.import_tasks import run_import_batch

        run_import_batch.delay(str(batch_id))
        return batch


    # ============================================================
    # retry（原子 claim 互斥 NF-3 + 两类失败分流 FB-E）
    # ============================================================

    async def retry(self, batch_id: UUID, user: User) -> ImportBatch:
        """重试导入批次（仅 partial / failed 可重试，retry_count<3）。

        FB-E 两类分流：
        - partial（有 failed 行）→ only_failed=True，仅重跑失败行（原地更新 attempt_count）
        - failed（解析失败 / 全行失败，无 failed 行）→ only_failed=False，整文件重跑

        NF-3 原子 claim：``claim_for_retry`` UPDATE WHERE status IN(partial,failed)
        RETURNING 保证同一 batch 同时只有一个 runner（防并发 retry / 重复点击）。

        Raises:
            ImportBatchNotFoundError(404) / ImportRetryExhaustedError(409) /
            ImportBatchBusyError(409)
        """
        batch = await self._repo.get_by_id(batch_id)
        if batch is None:
            raise ImportBatchNotFoundError(batch_id)

        claimed = await self._repo.claim_for_retry(batch_id, user.tenant_id)
        if claimed is None:
            # 区分耗尽 vs 忙碌（claim 返回 0 行的两种原因）
            if batch.retry_count >= 3:
                raise ImportRetryExhaustedError(batch_id)
            raise ImportBatchBusyError(batch_id)

        await self._audit.log(
            action="import.retry",
            resource="import_batch",
            resource_id=batch_id,
            after={"retry_count": claimed.retry_count},
            user_id=user.id,
        )
        await self._session.commit()
        import_retry_total.labels(source=claimed.source).inc()

        # FB-E 两类分流：有 failed 行 → 仅重跑失败行；否则整文件重跑
        only_failed = claimed.failed > 0
        countdown = {1: 1, 2: 5, 3: 30}.get(claimed.retry_count, 30)

        from app.tasks.import_tasks import run_import_batch

        run_import_batch.apply_async(
            args=[str(batch_id)],
            kwargs={"only_failed": only_failed},
            countdown=countdown,
        )
        return claimed

    # ============================================================
    # Read
    # ============================================================

    async def get_batch(self, batch_id: UUID, user: User) -> ImportBatch:
        batch = await self._repo.get_by_id(batch_id)
        if batch is None or batch.tenant_id != user.tenant_id:
            raise ImportBatchNotFoundError(batch_id)
        return batch

    async def list_batches(
        self,
        *,
        filters: ImportBatchListFilters,
        page: int,
        page_size: int,
        user: User,
    ) -> tuple[Sequence[ImportBatch], int]:
        return await self._repo.list_with_filters(
            tenant_id=user.tenant_id,
            filters=filters,
            page=page,
            page_size=page_size,
        )

    # ============================================================
    # download_errors（失败明细 CSV + csv_safe injection 防护）
    # ============================================================

    async def build_error_csv(self, batch_id: UUID, user: User) -> bytes:
        """生成失败明细 CSV（UTF-8 BOM + csv_safe 危险前缀转义）。

        列：row_number / error_detail / attempt_count / raw_data(JSON)。
        raw_data 各值与 error_detail 经 ``csv_safe`` 防 Excel 公式注入。
        """
        batch = await self.get_batch(batch_id, user)  # 404 + 租户校验
        failed_jobs = await self._job_repo.list_failed(batch.id)

        buf = io.StringIO()
        buf.write("\ufeff")  # UTF-8 BOM（Excel 中文不乱码）
        writer = csv.writer(buf)
        writer.writerow(["row_number", "error_detail", "attempt_count", "raw_data"])
        for job in failed_jobs:
            writer.writerow(
                [
                    job.row_number,
                    csv_safe(job.error_detail),
                    job.attempt_count,
                    csv_safe(json.dumps(job.raw_data, ensure_ascii=False)),
                ]
            )
        return buf.getvalue().encode("utf-8")


    # ============================================================
    # Private helpers
    # ============================================================

    @staticmethod
    def _assert_format(filename: str | None, content_type: str | None) -> None:
        """扩展名 + MIME 双白名单（P-U06a-05）。"""
        name = (filename or "").lower()
        if not name.endswith(_ALLOWED_EXT):
            raise ImportFormatUnsupportedError()
        if content_type and content_type.lower() not in _ALLOWED_MIME:
            raise ImportFormatUnsupportedError()

    async def _resolve_mapping_version(
        self, source: str, mapping_version: int | None, tenant_id: UUID
    ) -> int | None:
        """解析映射版本：指定 → 校验存在；None → 用 active（可为空）。"""
        from app.modules.importer.repository import FieldMappingRepository

        repo = FieldMappingRepository(self._session)
        if mapping_version is not None:
            mapping = await repo.get_by_version(tenant_id, source, mapping_version)
            if mapping is None:
                raise ImportMappingVersionNotFoundError()
            return mapping_version
        active = await repo.get_active(tenant_id, source)
        return active.version if active else None


__all__ = ["ImportService"]
