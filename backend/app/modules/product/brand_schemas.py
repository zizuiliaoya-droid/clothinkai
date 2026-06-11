"""Brand 字典 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BrandCreate(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    brand_code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_\-]+$")
    brand_name: str = Field(min_length=1, max_length=128)


class BrandUpdate(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    brand_name: str | None = Field(default=None, min_length=1, max_length=128)
    is_active: bool | None = None


class BrandResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    brand_code: str
    brand_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


__all__ = ["BrandCreate", "BrandResponse", "BrandUpdate"]
