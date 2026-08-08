"""U13 采集 Worker API（/api/crawler/tasks）。

worker_token 鉴权（X-Worker-Token + IP allowlist），独立于用户 JWT。
poll / exchange / result —— 凭据明文仅在 exchange 响应返回（一次性）。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import Response

from app.core.config import settings
from app.modules.collect.deps import CrawlerTaskServiceDep, WorkerTokenDep
from app.modules.collect.exceptions import CrawlerTaskResultInvalid
from app.modules.collect.schemas import (
    CredExchangeRequest,
    CredExchangeResponse,
    CrawlerTaskAssignment,
)

router = APIRouter(prefix="/api/crawler/tasks", tags=["crawler"])


async def _read_result_upload(file: UploadFile) -> bytes:
    """分块读取采集结果，超限立即中止，避免请求体整包进入内存。"""
    max_bytes = settings.IMPORT_MAX_FILE_MB * 1024 * 1024
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            from app.modules.importer.exceptions import ImportFileTooLargeError

            raise ImportFileTooLargeError()
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise CrawlerTaskResultInvalid()
    return content


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
    wt: WorkerTokenDep,
    service: CrawlerTaskServiceDep,
) -> CredExchangeResponse:
    """EP07-S04/§2.2.1 一次性 cred_token 换取明文凭据（不写日志）。"""
    return await service.exchange_credential(task_id, payload.cred_token, wt)


@router.post("/{task_id}/result")
async def report_result(
    task_id: UUID,
    wt: WorkerTokenDep,
    service: CrawlerTaskServiceDep,
    lease_token: str = Form(...),
    status_value: Literal["success", "failed"] = Form(..., alias="status"),
    error: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
) -> dict:
    """EP07-S11~S13 Worker 上传采集结果（success→触发导入 / failed→联动凭据）。"""
    content: bytes | None = None
    filename: str | None = None
    content_type: str | None = None
    if status_value == "success":
        if file is None:
            raise CrawlerTaskResultInvalid()
        content = await _read_result_upload(file)
        filename = file.filename
        content_type = file.content_type
    return await service.report_result(
        task_id,
        status_value,
        wt,
        lease_token=lease_token,
        content=content,
        filename=filename,
        content_type=content_type,
        error=error,
    )


__all__ = ["router"]
