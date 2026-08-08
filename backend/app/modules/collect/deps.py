"""U13 采集模块依赖注入（含 Worker Token 鉴权）。"""

from __future__ import annotations

from typing import Annotated, AsyncIterator

from fastapi import Depends, Header, Request
from sqlalchemy import text

from app.core.db import AsyncSessionBypass
from app.core.tenancy import (
    actor_type_ctx,
    bypass_rls_ctx,
    tenant_id_ctx,
    user_id_ctx,
)
from app.modules.auth.deps import SessionDep
from app.modules.collect.crawler_task_service import CrawlerTaskService
from app.modules.collect.data_quality_service import DataQualityService
from app.modules.collect.exceptions import WorkerTokenInvalid
from app.modules.collect.models import WorkerToken
from app.modules.collect.worker_token_service import WorkerTokenService


def get_worker_token_service(session: SessionDep) -> WorkerTokenService:
    """用户管理端使用普通租户会话。"""
    return WorkerTokenService(session)


WorkerTokenServiceDep = Annotated[
    WorkerTokenService, Depends(get_worker_token_service)
]


def get_data_quality_service(session: SessionDep) -> DataQualityService:
    return DataQualityService(session)


DataQualityServiceDep = Annotated[
    DataQualityService, Depends(get_data_quality_service)
]


async def get_worker_token(
    request: Request,
    x_worker_token: Annotated[str | None, Header()] = None,
) -> AsyncIterator[WorkerToken]:
    """跨租户鉴权后建立受限租户上下文，业务查询仍走 RLS 会话。"""
    if not x_worker_token:
        raise WorkerTokenInvalid("缺少 X-Worker-Token 头")
    client_ip = request.client.host if request.client else ""
    bypass_token = bypass_rls_ctx.set(True)
    try:
        async with AsyncSessionBypass() as session:
            await session.execute(text("SET LOCAL app.bypass_rls = 'on'"))
            worker = await WorkerTokenService(session).authenticate(
                x_worker_token, client_ip
            )
    finally:
        bypass_rls_ctx.reset(bypass_token)

    tenant_token = tenant_id_ctx.set(worker.tenant_id)
    bypass_business_token = bypass_rls_ctx.set(False)
    actor_token = actor_type_ctx.set("worker")
    user_token = user_id_ctx.set(None)
    try:
        yield worker
    finally:
        user_id_ctx.reset(user_token)
        actor_type_ctx.reset(actor_token)
        bypass_rls_ctx.reset(bypass_business_token)
        tenant_id_ctx.reset(tenant_token)


WorkerTokenDep = Annotated[WorkerToken, Depends(get_worker_token)]


def get_crawler_task_service(
    _worker: WorkerTokenDep,
    session: SessionDep,
) -> CrawlerTaskService:
    """先完成 Worker 鉴权和租户上下文建立，再创建 RLS 业务会话。"""
    return CrawlerTaskService(session)


CrawlerTaskServiceDep = Annotated[
    CrawlerTaskService, Depends(get_crawler_task_service)
]


__all__ = [
    "CrawlerTaskServiceDep",
    "DataQualityServiceDep",
    "WorkerTokenDep",
    "WorkerTokenServiceDep",
    "get_worker_token",
]
