"""U13 采集仓储层。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.collect.models import (
    CrawlerTask,
    DataQualityIssue,
    WorkerToken,
)


class WorkerTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, token: WorkerToken) -> None:
        self._session.add(token)

    async def get_active_by_hash(
        self, token_hash: str, *, for_update: bool = False
    ) -> WorkerToken | None:
        stmt = select(WorkerToken).where(
            WorkerToken.token_hash == token_hash,
            WorkerToken.is_active.is_(True),
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(
        self, token_id: UUID, tenant_id: UUID
    ) -> WorkerToken | None:
        stmt = select(WorkerToken).where(
            WorkerToken.id == token_id,
            WorkerToken.tenant_id == tenant_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list(self, tenant_id: UUID) -> Sequence[WorkerToken]:
        """列出指定租户的 Worker Token；显式租户条件作为 RLS 之外的防线。"""
        stmt = (
            select(WorkerToken)
            .where(WorkerToken.tenant_id == tenant_id)
            .order_by(WorkerToken.created_at.desc())
        )
        return (await self._session.execute(stmt)).scalars().all()


class CrawlerTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, task_id: UUID) -> CrawlerTask | None:
        return await self._session.get(CrawlerTask, task_id)

    async def get_for_worker(
        self,
        task_id: UUID,
        worker: WorkerToken,
        *,
        for_update: bool = False,
    ) -> CrawlerTask | None:
        """按租户和领取 Worker 显式绑定任务，可选行锁保护终态副作用。"""
        stmt = select(CrawlerTask).where(
            CrawlerTask.id == task_id,
            CrawlerTask.tenant_id == worker.tenant_id,
            CrawlerTask.worker_token_id == worker.id,
        )
        if for_update:
            stmt = stmt.with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()


class DataQualityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, issue: DataQualityIssue) -> None:
        self._session.add(issue)

    async def get(self, issue_id: UUID) -> DataQualityIssue | None:
        return await self._session.get(DataQualityIssue, issue_id)

    async def list(
        self,
        *,
        tenant_id: UUID,
        source: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[Sequence[DataQualityIssue], int]:
        stmt = select(DataQualityIssue).where(
            DataQualityIssue.tenant_id == tenant_id
        )
        count_stmt = (
            select(func.count())
            .select_from(DataQualityIssue)
            .where(DataQualityIssue.tenant_id == tenant_id)
        )
        for col, val in (
            (DataQualityIssue.source, source),
            (DataQualityIssue.severity, severity),
            (DataQualityIssue.status, status),
        ):
            if val is not None:
                stmt = stmt.where(col == val)
                count_stmt = count_stmt.where(col == val)
        stmt = (
            stmt.order_by(DataQualityIssue.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        total = int((await self._session.execute(count_stmt)).scalar_one())
        return items, total

    async def summary(self, tenant_id: UUID) -> list[dict]:
        stmt = (
            select(
                DataQualityIssue.source,
                DataQualityIssue.severity,
                func.count().label("cnt"),
            )
            .where(
                DataQualityIssue.tenant_id == tenant_id,
                DataQualityIssue.status == "open",
            )
            .group_by(DataQualityIssue.source, DataQualityIssue.severity)
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {"source": r.source, "severity": r.severity, "count": int(r.cnt)}
            for r in rows
        ]


__all__ = [
    "CrawlerTaskRepository",
    "DataQualityRepository",
    "WorkerTokenRepository",
]
