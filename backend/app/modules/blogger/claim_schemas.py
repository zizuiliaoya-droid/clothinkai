from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClaimSelfItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    blogger_id: UUID
    wechat: str = Field(min_length=1, max_length=64)
    work_wechat_id: UUID


class ClaimSelfRequest(BaseModel):
    items: list[ClaimSelfItem] = Field(min_length=1, max_length=100)


class ClaimSelfResult(BaseModel):
    blogger_id: UUID
    status: Literal["claimed", "already_claimed", "not_verified"]


class ClaimAssignItem(BaseModel):
    blogger_id: UUID
    pr_id: UUID
    work_wechat_id: UUID


class ClaimAssignRequest(BaseModel):
    items: list[ClaimAssignItem] = Field(min_length=1, max_length=100)


class ClaimWorkWechatUpdate(BaseModel):
    work_wechat_id: UUID


class ClaimTransferRequest(ClaimWorkWechatUpdate):
    to_pr_id: UUID


class ClaimResponse(BaseModel):
    id: UUID
    blogger_id: UUID
    pr_id: UUID
    pr_name: str
    method: str
    operated_by: UUID
    created_at: datetime
    work_wechat_id: UUID | None
    work_wechat_name: str | None
    work_wechat_active: bool | None


class WorkWechatCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    value: str = Field(min_length=1, max_length=64)


class WorkWechatUpdate(BaseModel):
    is_active: bool


class WorkWechatItem(BaseModel):
    id: UUID
    value: str
    is_active: bool


class WorkWechatPage(BaseModel):
    items: list[WorkWechatItem]
    can_manage: bool


class ClaimSettings(BaseModel):
    enabled_at: datetime | None
    unclaimed_count: int
    pending_work_wechat_count: int
    can_enable: bool
