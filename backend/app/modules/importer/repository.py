"""U06a importer 仓储层。

3 个 Repository：
- ImportBatchRepository（含 **claim_for_retry 原子互斥 NF-3** + find_by_hash + list）
- ImportJobRepository（行级 upsert + 失败行查询 + 原地更新 attempt_count）
- FieldMappingRepository（版本查询 + active 切换）

自动应用 RLS（依赖 Session 注入 / SET LOCAL tenant_id）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.importer.models import FieldMapping, ImportBatch, ImportJob


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ImportBatchListFilters:
    source: str | None = None
    status: str | None = None
    created_at_from: date | None = None
    created_at_to: date | None = None


# ---------------------------------------------------------------------------
# ImportBatchRepository
# ---------------------------------------------------------------------------


class ImportBatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, batch: ImportBatch) -> None:
        self._session.add(batch)

    async def get_by_id(self, batch_id: UUID) -> ImportBatch | None:
        return await self._session.get(ImportBatch, batch_id)

    async def find_by_hash(
        self, tenant_id: UUID, source: str, file_hash: str
    ) -> ImportBatch | None:
        """去重命中查询（IntegrityError 后取已有 batch 给 409 提示，NF-2）。"""
        stmt = select(ImportBatch).where(
            ImportBatch.tenant_id == tenant_id,
            ImportBatch.source == source,
            ImportBatch.file_hash == file_hash,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def claim_for_retry(
        self, batch_id: UUID, tenant_id: UUID
    ) -> ImportBatch | None:
        """NF-3 原子领取：仅 partial/failed 且 retry_count<3 可领，置 processing + retry_count+1。

        0 行 = 已在跑 / 已耗尽 / 状态不符 → service 转 409（区分 busy vs exhausted）。
        保证同一 batch 同一时刻只有一个 runner（防并发 retry / 重复点击）。
        """
        stmt = (
            update(ImportBatch)
            .where(
                ImportBatch.id == batch_id,
                ImportBatch.tenant_id == tenant_id,
                ImportBatch.status.in_(["partial", "failed"]),
                ImportBatch.retry_count < 3,
            )
            .values(
                status="processing",
                retry_count=ImportBatch.retry_count + 1,
                updated_at=func.now(),
            )
            .returning(ImportBatch)
            .execution_options(synchronize_session=False)
        )
        row = (await self._session.execute(stmt)).fetchone()
        if row is None:
            return None
        batch: ImportBatch = row[0]
        await self._session.refresh(batch)
        return batch

    async def update_summary(
        self,
        *,
        batch_id: UUID,
        status: str,
        total_rows: int | None = None,
        imported: int | None = None,
        failed: int | None = None,
        error_summary: str | None = None,
    ) -> None:
        """run_import_batch 汇总段更新 batch 计数 + 终态。"""
        values: dict[str, Any] = {"status": status, "updated_at": func.now()}
        if total_rows is not None:
            values["total_rows"] = total_rows
        if imported is not None:
            values["imported"] = imported
        if failed is not None:
            values["failed"] = failed
        if error_summary is not None:
            values["error_summary"] = error_summary
        await self._session.execute(
            update(ImportBatch)
            .where(ImportBatch.id == batch_id)
            .values(**values)
            .execution_options(synchronize_session=False)
        )

    async def list_with_filters(
        self,
        *,
        tenant_id: UUID,
        filters: ImportBatchListFilters,
        page: int,
        page_size: int,
    ) -> tuple[Sequence[ImportBatch], int]:
        stmt = select(ImportBatch).where(ImportBatch.tenant_id == tenant_id)
        if filters.source:
            stmt = stmt.where(ImportBatch.source == filters.source)
        if filters.status:
            stmt = stmt.where(ImportBatch.status == filters.status)
        if filters.created_at_from:
            stmt = stmt.where(ImportBatch.created_at >= filters.created_at_from)
        if filters.created_at_to:
            stmt = stmt.where(
                ImportBatch.created_at < filters.created_at_to + timedelta(days=1)
            )

        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self._session.execute(total_stmt)).scalar_one())

        stmt = (
            stmt.order_by(ImportBatch.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        return items, total


# ---------------------------------------------------------------------------
# ImportJobRepository
# ---------------------------------------------------------------------------


class ImportJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, job: ImportJob) -> None:
        self._session.add(job)

    async def list_failed(self, batch_id: UUID) -> Sequence[ImportJob]:
        """失败行（下载明细 + only_failed 重试用）。"""
        stmt = (
            select(ImportJob)
            .where(
                ImportJob.batch_id == batch_id,
                ImportJob.status == "failed",
            )
            .order_by(ImportJob.row_number.asc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def get_by_batch_row(
        self, batch_id: UUID, row_number: int
    ) -> ImportJob | None:
        stmt = select(ImportJob).where(
            ImportJob.batch_id == batch_id,
            ImportJob.row_number == row_number,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def count_by_status(self, batch_id: UUID, status: str) -> int:
        stmt = select(func.count()).where(
            ImportJob.batch_id == batch_id, ImportJob.status == status
        )
        return int((await self._session.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# FieldMappingRepository
# ---------------------------------------------------------------------------


class FieldMappingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, mapping: FieldMapping) -> None:
        self._session.add(mapping)

    async def get_active(
        self, tenant_id: UUID, source: str
    ) -> FieldMapping | None:
        stmt = (
            select(FieldMapping)
            .where(
                FieldMapping.tenant_id == tenant_id,
                FieldMapping.source == source,
                FieldMapping.is_active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_version(
        self, tenant_id: UUID, source: str, version: int
    ) -> FieldMapping | None:
        stmt = (
            select(FieldMapping)
            .where(
                FieldMapping.tenant_id == tenant_id,
                FieldMapping.source == source,
                FieldMapping.version == version,
            )
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_versions(
        self, tenant_id: UUID, source: str
    ) -> Sequence[FieldMapping]:
        stmt = (
            select(FieldMapping)
            .where(
                FieldMapping.tenant_id == tenant_id,
                FieldMapping.source == source,
            )
            .order_by(FieldMapping.version.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()

    async def next_version(self, tenant_id: UUID, source: str) -> int:
        stmt = select(func.coalesce(func.max(FieldMapping.version), 0)).where(
            FieldMapping.tenant_id == tenant_id,
            FieldMapping.source == source,
        )
        return int((await self._session.execute(stmt)).scalar_one()) + 1

    async def deactivate_active(self, tenant_id: UUID, source: str) -> None:
        """新建版本前把旧 active 置 false（EP07-S09）。"""
        await self._session.execute(
            update(FieldMapping)
            .where(
                FieldMapping.tenant_id == tenant_id,
                FieldMapping.source == source,
                FieldMapping.is_active.is_(True),
            )
            .values(is_active=False, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )


__all__ = [
    "FieldMappingRepository",
    "ImportBatchListFilters",
    "ImportBatchRepository",
    "ImportJobRepository",
]
