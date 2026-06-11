"""U03 blogger 模块 Pydantic Schemas。

字段命名映射到 ORM；service 层 ``to_response`` 时按角色过滤敏感字段
（quote / wechat / phone）。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.blogger.enums import BloggerType, GenderTarget, Platform


_QuoteField = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=10, decimal_places=2),
]


def _validate_tag_items(v: list[str] | None) -> list[str] | None:
    if v is None:
        return None
    if any(len(t) > 32 or not t.strip() for t in v):
        raise ValueError("每个 tag 长度需 1-32")
    return [t.strip() for t in v]


class BloggerBase(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    nickname: str = Field(min_length=1, max_length=128)
    platform: Platform = Platform.XIAOHONGSHU
    wechat: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    follower_count: int | None = Field(default=None, ge=0)
    blogger_type: BloggerType | None = None
    gender_target: GenderTarget | None = None
    category_tags: list[str] = Field(default_factory=list, max_length=20)
    quality_tags: list[str] = Field(default_factory=list, max_length=20)
    quote: _QuoteField | None = None
    cooperation_history: str | None = None
    remark: str | None = None
    is_suspected_fake: bool = False


class BloggerCreate(BloggerBase):
    xiaohongshu_id: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"
    )

    @field_validator("category_tags", "quality_tags")
    @classmethod
    def _validate_tags(cls, v: list[str]) -> list[str]:
        result = _validate_tag_items(v)
        return result if result is not None else []


class BloggerUpdate(BaseModel):
    """部分更新（PATCH 语义）。"""

    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    xiaohongshu_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"
    )
    nickname: str | None = Field(default=None, min_length=1, max_length=128)
    platform: Platform | None = None
    wechat: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    follower_count: int | None = Field(default=None, ge=0)
    blogger_type: BloggerType | None = None
    gender_target: GenderTarget | None = None
    category_tags: list[str] | None = Field(default=None, max_length=20)
    quality_tags: list[str] | None = Field(default=None, max_length=20)
    quote: _QuoteField | None = None
    cooperation_history: str | None = None
    remark: str | None = None
    is_suspected_fake: bool | None = None
    is_active: bool | None = None

    @field_validator("category_tags", "quality_tags")
    @classmethod
    def _validate_tags(cls, v: list[str] | None) -> list[str] | None:
        return _validate_tag_items(v)


class BloggerResponse(BaseModel):
    """博主响应。

    敏感字段（quote / wechat / phone）按角色过滤
    （详见 ``service.BloggerService.to_response``）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    xiaohongshu_id: str
    nickname: str
    platform: str
    # 敏感字段 — service 层按角色填 None 屏蔽
    wechat: str | None = None
    phone: str | None = None
    follower_count: int | None = None
    blogger_type: str | None = None
    gender_target: str | None = None
    category_tags: list[str] = Field(default_factory=list)
    quality_tags: list[str] = Field(default_factory=list)
    quote: Decimal | None = None  # 敏感
    cooperation_history: str | None = None
    remark: str | None = None
    is_suspected_fake: bool
    is_active: bool
    is_deleted: bool
    # U11：受众画像（U13 写入）+ 读时衍生点赞/阅读比
    audience_profile: dict | None = None
    read_like_ratio: Decimal | None = None
    created_at: datetime
    updated_at: datetime


class BloggerPage(BaseModel):
    items: list[BloggerResponse]
    total: int
    page: int
    page_size: int


__all__ = [
    "BloggerBase",
    "BloggerCreate",
    "BloggerPage",
    "BloggerResponse",
    "BloggerUpdate",
]
