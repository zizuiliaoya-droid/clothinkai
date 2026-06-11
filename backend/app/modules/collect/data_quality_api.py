"""U13 数据质量看板 API（/api/data-quality）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.collect.deps import DataQualityServiceDep
from app.modules.collect.schemas import (
    DqIssue,
    DqIssuePage,
    DqResolveRequest,
    DqSummaryRow,
)

router = APIRouter(prefix="/api/data-quality", tags=["data-quality"])


@router.get(
    "/summary",
    response_model=list[DqSummaryRow],
    dependencies=[require_permission("data_quality", "read")],
)
async def summary(
    user: CurrentActiveUser,
    service: DataQualityServiceDep,
) -> list[DqSummaryRow]:
    """EP07-S14 按 source × severity 分组计数。"""
    rows = await service.summary(user.tenant_id)
    return [DqSummaryRow(**r) for r in rows]


@router.get(
    "/issues",
    response_model=DqIssuePage,
    dependencies=[require_permission("data_quality", "read")],
)
async def list_issues(
    user: CurrentActiveUser,
    service: DataQualityServiceDep,
    source: Annotated[str | None, Query(max_length=32)] = None,
    severity: Annotated[str | None, Query(max_length=8)] = None,
    issue_status: Annotated[str | None, Query(max_length=8)] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DqIssuePage:
    return await service.list_issues(
        tenant_id=user.tenant_id,
        source=source,
        severity=severity,
        status=issue_status,
        page=page,
        page_size=page_size,
    )


@router.put(
    "/issues/{issue_id}",
    response_model=DqIssue,
    dependencies=[require_permission("data_quality", "write")],
)
async def resolve_issue(
    issue_id: UUID,
    payload: DqResolveRequest,
    user: CurrentActiveUser,
    service: DataQualityServiceDep,
) -> DqIssue:
    return await service.resolve(issue_id, payload.status, user)


__all__ = ["router"]
