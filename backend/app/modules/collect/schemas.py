"""U13 采集模块 Pydantic Schemas。"""

from __future__ import annotations

from datetime import date, datetime
from ipaddress import ip_address, ip_network
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.collect.enums import CrawlerPlatform


# ----------------------------- WorkerToken ----------------------------- #


class WorkerTokenCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., min_length=1, max_length=64)
    ip_allowlist: list[str] = Field(..., min_length=1, max_length=32)

    @field_validator("ip_allowlist")
    @classmethod
    def validate_ip_allowlist(cls, values: list[str]) -> list[str]:
        """校验并规范化单 IP/CIDR；空白名单不允许签发可用 Token。"""
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            try:
                parsed = str(ip_network(item, strict=False)) if "/" in item else str(ip_address(item))
            except ValueError as exc:
                raise ValueError(f"无效的 IP 或 CIDR: {item}") from exc
            if parsed not in normalized:
                normalized.append(parsed)
        if not normalized:
            raise ValueError("IP 白名单至少需要一个 IP 或 CIDR")
        return normalized


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
    status: Literal["success", "failed"]
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
