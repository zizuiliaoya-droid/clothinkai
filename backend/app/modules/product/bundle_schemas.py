"""U17 套装/组合商品 Schema。"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class BundleItemIn(BaseModel):
    sku_id: UUID
    quantity: int = Field(..., ge=1)


class BundleCreate(BaseModel):
    bundle_code: str = Field(..., min_length=1, max_length=64)
    bundle_name: str = Field(..., min_length=1, max_length=255)
    remark: str | None = None
    items: list[BundleItemIn] = Field(..., min_length=1)


class BundleItemResponse(BaseModel):
    sku_id: UUID
    quantity: int


class BundleResponse(BaseModel):
    id: UUID
    bundle_code: str
    bundle_name: str
    remark: str | None = None
    is_active: bool
    items: list[BundleItemResponse]


__all__ = [
    "BundleCreate",
    "BundleItemIn",
    "BundleItemResponse",
    "BundleResponse",
]
