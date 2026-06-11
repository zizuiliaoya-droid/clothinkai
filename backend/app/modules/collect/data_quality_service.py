"""U13 DataQualityService（记录/汇总/列表/处理）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import data_quality_issue_total
from app.modules.auth.models import User
from app.modules.collect.exceptions import DqIssueNotFound
from app.modules.collect.models import DataQualityIssue
from app.modules.collect.repository import DataQualityRepository
from app.modules.collect.schemas import DqIssue, DqIssuePage


class DataQualityService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = DataQualityRepository(session)
        self._audit = AuditService(session)

    async def record(
        self,
        *,
        source: str,
        severity: str,
        message: str,
        entity_type: str | None = None,
        entity_ref: str | None = None,
    ) -> None:
        """记录一条数据质量异常（不自行 commit，复用调用方/runner 事务）。"""
        issue = DataQualityIssue(
            source=source,
            severity=severity,
            status="open",
            entity_type=entity_type,
            entity_ref=entity_ref,
            message=message,
        )
        self._repo.add(issue)
        await self._session.flush()
        data_quality_issue_total.labels(source, severity).inc()

    async def summary(self, tenant_id: UUID) -> list[dict]:
        return await self._repo.summary(tenant_id)

    async def list_issues(
        self,
        *,
        tenant_id: UUID,
        source: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> DqIssuePage:
        items, total = await self._repo.list(
            tenant_id=tenant_id,
            source=source,
            severity=severity,
            status=status,
            page=page,
            page_size=page_size,
        )
        return DqIssuePage(
            items=[DqIssue.model_validate(i) for i in items],
            total=total,
            page=page,
            page_size=page_size,
        )

    async def resolve(self, issue_id: UUID, status: str, user: User) -> DqIssue:
        issue = await self._repo.get(issue_id)
        if issue is None:
            raise DqIssueNotFound()
        issue.status = status
        await self._session.flush()
        await self._audit.log(
            action="data_quality.resolve",
            resource="data_quality_issue",
            resource_id=issue.id,
            after={"status": status},
            user_id=user.id,
        )
        await self._session.commit()
        return DqIssue.model_validate(issue)


__all__ = ["DataQualityService"]
