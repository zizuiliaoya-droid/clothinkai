"""U10b 平台商品映射 Pydantic Schema。

「平台链接」= 店铺里一条实际在卖的链接（千牛商品ID / 万相台主体ID）。它连接三样东西：
商品（``goods_main_id``，决定报表归属）、款式（``style_id``，仓库发的是哪件衣服）、
渠道（``channel``，普通还是直播，用于直播分账）。

这层是运维视图专用 —— 业务页面不该看到平台ID，所以字段在这里暴露而不在款式管理页。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

CHANNELS = ("普通", "直播")


class PlatformProductCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    platform: str = Field(..., min_length=1, max_length=16)
    platform_id: str = Field(..., min_length=1, max_length=64)
    style_id: UUID
    sku_id: UUID | None = None
    goods_main_id: UUID | None = None
    """链接归属的商品。不传由服务端取该款式的主商品（非套装优先）。"""
    channel: str = Field(default="普通", pattern="^(普通|直播)$")
    title: str | None = Field(default=None, max_length=255)


class PlatformProductUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    style_id: UUID | None = None
    sku_id: UUID | None = None
    goods_main_id: UUID | None = None
    channel: str | None = Field(default=None, pattern="^(普通|直播)$")
    title: str | None = None
    is_active: bool | None = None


class PlatformProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    platform: str
    platform_id: str
    style_id: UUID
    sku_id: UUID | None
    goods_main_id: UUID | None = None
    channel: str = "普通"
    title: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    # 运维视图要能一眼看出这条链接连到哪个商品、哪件衣服。实时取，不做快照。
    goods_code: str | None = None
    goods_title: str | None = None
    goods_is_suit: bool = False
    style_code: str | None = None
    style_name: str | None = None


class PlatformProductListResponse(BaseModel):
    items: list[PlatformProductResponse]
    total: int
    page: int = 1
    page_size: int = 20


__all__ = [
    "CHANNELS",
    "PlatformProductCreate",
    "PlatformProductListResponse",
    "PlatformProductResponse",
    "PlatformProductUpdate",
]
