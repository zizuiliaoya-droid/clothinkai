"""U13 Worker Token 管理 API（/api/crawler/worker-tokens，管理员）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.collect.deps import WorkerTokenServiceDep
from app.modules.collect.schemas import WorkerTokenCreate, WorkerTokenIssued

router = APIRouter(prefix="/api/crawler/worker-tokens", tags=["crawler"])


@router.post(
    "/",
    response_model=WorkerTokenIssued,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("crawler.worker", "write")],
)
async def issue_worker_token(
    payload: WorkerTokenCreate,
    user: CurrentActiveUser,
    service: WorkerTokenServiceDep,
) -> WorkerTokenIssued:
    """签发 Worker Token（明文 token 仅此响应返回一次）。"""
    wt, raw = await service.issue(payload.name, payload.ip_allowlist, user)
    return WorkerTokenIssued(
        id=wt.id,
        name=wt.name,
        ip_allowlist=wt.ip_allowlist,
        is_active=wt.is_active,
        consecutive_auth_failures=wt.consecutive_auth_failures,
        last_seen_at=wt.last_seen_at,
        created_at=wt.created_at,
        updated_at=wt.updated_at,
        token=raw,
    )


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[require_permission("crawler.worker", "write")],
)
async def revoke_worker_token(
    token_id: UUID,
    user: CurrentActiveUser,
    service: WorkerTokenServiceDep,
) -> dict[str, bool]:
    await service.revoke(token_id, user)
    return {"ok": True}


__all__ = ["router"]
