"""U13 采集 Worker API（/api/crawler/tasks）。

worker_token 鉴权（X-Worker-Token + IP allowlist），独立于用户 JWT。
poll / exchange / result —— 凭据明文仅在 exchange 响应返回（一次性）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import Response

from app.modules.collect.deps import CrawlerTaskServiceDep, WorkerTokenDep
from app.modules.collect.schemas import (
    CredExchangeRequest,
    CredExchangeResponse,
    CrawlerTaskAssignment,
)

router = APIRouter(prefix="/api/crawler/tasks", tags=["crawler"])


@router.post("/poll", response_model=None)
async def poll_task(
    wt: WorkerTokenDep,
    service: CrawlerTaskServiceDep,
) -> CrawlerTaskAssignment | Response:
    """EP07-S11~S13 Worker 领取一个 pending 任务（无 pending → 204）。"""
    assignment = await service.poll_next_task(wt)
    if assignment is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return assignment


@router.post("/{task_id}/exchange", response_model=CredExchangeResponse)
async def exchange_credential(
    task_id: UUID,
    payload: CredExchangeRequest,
    _wt: WorkerTokenDep,
    service: CrawlerTaskServiceDep,
) -> CredExchangeResponse:
    """EP07-S04/§2.2.1 一次性 cred_token 换取明文凭据（不写日志）。"""
    return await service.exchange_credential(task_id, payload.cred_token)


@router.post("/{task_id}/result")
async def report_result(
    task_id: UUID,
    _wt: WorkerTokenDep,
    service: CrawlerTaskServiceDep,
    status_value: str = Form(..., alias="status"),
    error: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    """EP07-S11~S13 Worker 上传采集结果（success→触发导入 / failed→联动凭据）。"""
    content = await file.read() if file is not None else None
    filename = file.filename if file is not None else None
    return await service.report_result(
        task_id,
        status_value,
        content=content,
        filename=filename,
        error=error,
    )


__all__ = ["router"]
