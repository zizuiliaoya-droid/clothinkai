"""U13 采集模块 Pydantic Schemas。"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.collect.enums import CrawlerPlatform


# ----------------------------- WorkerToken ----------------------------- #


class WorkerTokenCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=64)
    ip_allowlist: list[str] = Field(default_factory=list)


class WorkerTokenPublic(BaseModel):
    """worker_token 公开视图（不含明文 token / hash）。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    ip_allowlist: list[str]
    is_active: bool
    consecutive_auth_failures: int
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkerTokenIssued(WorkerTokenPublic):
    """签发响应（含明文 token，仅一次）。"""

    token: str


# ----------------------------- Crawler poll/exchange/result ----------------------------- #


class CrawlerTaskAssignment(BaseModel):
    """poll 响应：任务 + 一次性 cred_token（无明文密码）。"""

    task_id: UUID
    platform: str
    credential_id: UUID
    target_date: date
    cred_token: str
    expires_at: datetime


class CredExchangeRequest(BaseModel):
    cred_token: str = Field(..., min_length=1)


class CredExchangeResponse(BaseModel):
    """exchange 响应：明文凭据（仅此响应，不写日志）。"""

    username: str
    password: str


class CrawlerResultIn(BaseModel):
    status: str = Field(..., pattern=r"^(success|failed)$")
    error: str | None = None


# ----------------------------- DataQuality ----------------------------- #


class DqIssue(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source: str
    severity: str
    status: str
    entity_type: str | None
    entity_ref: str | None
    message: str
    created_at: datetime


class DqIssuePage(BaseModel):
    items: list[DqIssue]
    total: int
    page: int
    page_size: int


class DqSummaryRow(BaseModel):
    source: str
    severity: str
    count: int


class DqResolveRequest(BaseModel):
    status: str = Field(..., pattern=r"^(fixed|ignored)$")


__all__ = [
    "CrawlerPlatform",
    "CrawlerResultIn",
    "CrawlerTaskAssignment",
    "CredExchangeRequest",
    "CredExchangeResponse",
    "DqIssue",
    "DqIssuePage",
    "DqResolveRequest",
    "DqSummaryRow",
    "WorkerTokenCreate",
    "WorkerTokenIssued",
    "WorkerTokenPublic",
]
