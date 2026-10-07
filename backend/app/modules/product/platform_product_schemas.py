"""U10b 平台商品映射 Pydantic Schema。

「平台链接」= 店铺里一条实际在卖的链接（千牛商品ID / 万相台主体ID）。它连接三样东西：
商品（``goods_main_id``，决定报表归属）、款式（``style_id``，仓库发的是哪件衣服）、
渠道（``channel``，普通还是直播，用于直播分账）。

这层是运维视图专用 —— 业务页面不该看到平台ID，所以字段在这里暴露而不在款式管理页。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_core import PydanticCustomError

from app.modules.product.schemas import _normalize_platform_id

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

    @field_validator("platform_id")
    @classmethod
    def _clean_platform_id(cls, v: str) -> str:
        # 从 Excel 复制来的 ID 常带前导单引号（8a-5，AC 36）；与款式的千牛ID同一套规范化。
        # after 校验器的返回值不会再过类型校验，规范化后为空必须在这里拒掉（→ 422）。
        # 用 PydanticCustomError 而不是 ValueError：后者会把异常对象放进错误的 ctx，
        # 全局 422 处理器直接 JSONResponse 序列化不了，变成 500。
        cleaned = _normalize_platform_id(v)
        if cleaned is None:
            raise PydanticCustomError("platform_id_blank", "平台 ID 不能为空")
        return cleaned


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
    goods_short_name: str | None = None
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
