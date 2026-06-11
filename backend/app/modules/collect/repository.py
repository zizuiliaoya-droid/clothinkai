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

    async def get_active_by_hash(self, token_hash: str) -> WorkerToken | None:
        stmt = select(WorkerToken).where(
            WorkerToken.token_hash == token_hash,
            WorkerToken.is_active.is_(True),
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_id(self, token_id: UUID) -> WorkerToken | None:
        return await self._session.get(WorkerToken, token_id)


class CrawlerTaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, task_id: UUID) -> CrawlerTask | None:
        return await self._session.get(CrawlerTask, task_id)


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
            .where(DataQualityIssue.tenant_id == tenant_id)
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
