"""U13 采集模块依赖注入（含 Worker Token 鉴权）。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from app.modules.auth.deps import SessionDep
from app.modules.collect.crawler_task_service import CrawlerTaskService
from app.modules.collect.data_quality_service import DataQualityService
from app.modules.collect.exceptions import WorkerTokenInvalid
from app.modules.collect.models import WorkerToken
from app.modules.collect.worker_token_service import WorkerTokenService


def get_worker_token_service(session: SessionDep) -> WorkerTokenService:
    return WorkerTokenService(session)


WorkerTokenServiceDep = Annotated[
    WorkerTokenService, Depends(get_worker_token_service)
]


def get_crawler_task_service(session: SessionDep) -> CrawlerTaskService:
    return CrawlerTaskService(session)


CrawlerTaskServiceDep = Annotated[
    CrawlerTaskService, Depends(get_crawler_task_service)
]


def get_data_quality_service(session: SessionDep) -> DataQualityService:
    return DataQualityService(session)


DataQualityServiceDep = Annotated[
    DataQualityService, Depends(get_data_quality_service)
]


async def get_worker_token(
    request: Request,
    service: WorkerTokenServiceDep,
    x_worker_token: Annotated[str | None, Header()] = None,
) -> WorkerToken:
    """Worker API 鉴权：X-Worker-Token + IP allowlist（独立于用户 JWT）。"""
    if not x_worker_token:
        raise WorkerTokenInvalid("缺少 X-Worker-Token 头")
    client_ip = request.client.host if request.client else ""
    return await service.authenticate(x_worker_token, client_ip)


WorkerTokenDep = Annotated[WorkerToken, Depends(get_worker_token)]


__all__ = [
    "CrawlerTaskServiceDep",
    "DataQualityServiceDep",
    "WorkerTokenDep",
    "WorkerTokenServiceDep",
    "get_worker_token",
]
