"""U07 wecom Pydantic Schema。

关键：WecomConfigResponse **不回显** secret 明文（仅 secret_configured: bool）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 配置（EP08-S02）
# ---------------------------------------------------------------------------


class WecomConfigUpdate(BaseModel):
    corp_id: str = Field(min_length=1, max_length=64)
    agent_id: str = Field(min_length=1, max_length=32)
    secret: str = Field(min_length=1, description="应用 secret，加密存储不回显")
    callback_token: str | None = Field(default=None, max_length=64)
    callback_aes_key: str | None = Field(default=None, max_length=64)
    default_sender_userid: str | None = Field(default=None, max_length=64)
    is_active: bool = True


class WecomConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    corp_id: str
    agent_id: str
    secret_configured: bool
    callback_token: str | None = None
    default_sender_userid: str | None = None
    is_active: bool


class WecomTestResult(BaseModel):
    ok: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# 绑定（EP08-S03）
# ---------------------------------------------------------------------------


class WecomBindResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    blogger_id: UUID
    external_userid: str
    bound_at: datetime


# ---------------------------------------------------------------------------
# 模板（EP08-S04）
# ---------------------------------------------------------------------------


class TemplateUpdate(BaseModel):
    content: str = Field(min_length=1)


class TemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    template_type: str
    content: str


# ---------------------------------------------------------------------------
# 消息（EP08-S06）
# ---------------------------------------------------------------------------


class WecomMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    blogger_id: UUID
    pr_id: UUID | None = None
    template_type: str
    rendered_content: str
    status: str
    wecom_msgid: str | None = None
    error_detail: str | None = None
    sent_at: datetime | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# 通知（EP08-S07 支撑）
# ---------------------------------------------------------------------------


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    content: str
    link: str | None = None
    is_read: bool
    created_at: datetime


class UnreadCountResponse(BaseModel):
    count: int


__all__ = [
    "NotificationResponse",
    "TemplateResponse",
    "TemplateUpdate",
    "UnreadCountResponse",
    "WecomBindResponse",
    "WecomConfigResponse",
    "WecomConfigUpdate",
    "WecomMessageResponse",
    "WecomTestResult",
]
