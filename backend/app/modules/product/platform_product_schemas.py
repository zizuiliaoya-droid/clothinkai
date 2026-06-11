"""U10b 平台商品映射 Pydantic Schema。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlatformProductCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    platform: str = Field(..., min_length=1, max_length=16)
    platform_id: str = Field(..., min_length=1, max_length=64)
    style_id: UUID
    sku_id: UUID | None = None
    title: str | None = Field(default=None, max_length=255)


class PlatformProductUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    style_id: UUID | None = None
    sku_id: UUID | None = None
    title: str | None = None
    is_active: bool | None = None


class PlatformProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    platform: str
    platform_id: str
    style_id: UUID
    sku_id: UUID | None
    title: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class PlatformProductListResponse(BaseModel):
    items: list[PlatformProductResponse]
    total: int


__all__ = [
    "PlatformProductCreate",
    "PlatformProductListResponse",
    "PlatformProductResponse",
    "PlatformProductUpdate",
]
