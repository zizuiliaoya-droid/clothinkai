"""U02 product 模块 Pydantic Schemas。

按 functional-design/business-rules.md §2 / §6 + nfr-design/logical-components.md §1.1。

字段命名约定：
- 请求 schema 字段尽量映射到 ORM 同名字段
- 响应 schema 由 service 层调 ``to_response`` 时按角色过滤敏感字段（``cost_price`` / ``purchase_price``）

字段写权限（与角色硬编码绑定，详见 ``legacy_field_permissions.py``）：
- ``cost_price`` / ``purchase_price``：仅 admin / 跟单 / 财务 可写
- 其他字段：默认按 ``product:write`` 权限统一控制
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.product.enums import (
    Category,
    DesignStatus,
    Gender,
    Season,
    SourcingType,
)


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------


_PriceField = Annotated[
    Decimal,
    Field(ge=Decimal("0"), max_digits=10, decimal_places=2),
]


def _strip_or_none(v: str | None) -> str | None:
    if v is None:
        return None
    s = v.strip()
    return s or None


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


class StyleBase(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    style_name: str = Field(min_length=1, max_length=255)
    short_name: str | None = Field(default=None, max_length=64)
    brand_id: UUID | None = None
    category: Category
    season: Season | None = None
    gender: Gender | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)
    tag_color: list[str] = Field(default_factory=list, max_length=20)
    main_image_key: str | None = Field(default=None, max_length=512)
    remark: str | None = None
    owner_id: UUID | None = None
    design_status: DesignStatus = DesignStatus.BULK


class StyleCreate(StyleBase):
    style_code: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"
    )

    @field_validator("tags", "tag_color")
    @classmethod
    def _validate_tag_items(cls, v: list[str]) -> list[str]:
        if any(len(t) > 32 or not t.strip() for t in v):
            raise ValueError("每个 tag 长度需 1-32")
        return [t.strip() for t in v]


class StyleUpdate(BaseModel):
    """部分更新；style_code 可改但需通过唯一性校验。"""

    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    style_code: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"
    )
    style_name: str | None = Field(default=None, min_length=1, max_length=255)
    short_name: str | None = Field(default=None, max_length=64)
    brand_id: UUID | None = None
    category: Category | None = None
    season: Season | None = None
    gender: Gender | None = None
    tags: list[str] | None = Field(default=None, max_length=20)
    tag_color: list[str] | None = Field(default=None, max_length=20)
    main_image_key: str | None = Field(default=None, max_length=512)
    remark: str | None = None
    owner_id: UUID | None = None
    design_status: DesignStatus | None = None
    is_active: bool | None = None


class StyleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    style_code: str
    style_name: str
    short_name: str | None = None
    brand_id: UUID | None = None
    category: str
    season: str | None = None
    gender: str | None = None
    tags: list[str] = Field(default_factory=list)
    tag_color: list[str] = Field(default_factory=list)
    main_image_key: str | None = None
    main_image_url: str | None = None
    remark: str | None = None
    owner_id: UUID | None = None
    design_status: str
    is_active: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Sku
# ---------------------------------------------------------------------------


class SkuBase(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    color: str = Field(min_length=1, max_length=64)
    size: str = Field(min_length=1, max_length=32)
    cost_price: _PriceField | None = None
    purchase_price: _PriceField | None = None
    base_price: _PriceField | None = None
    sourcing_type: SourcingType = SourcingType.SELF_PRODUCED


class SkuCreate(SkuBase):
    style_id: UUID
    sku_code: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"
    )


class SkuUpdate(BaseModel):
    """部分更新。"""

    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    sku_code: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_\-]+$"
    )
    color: str | None = Field(default=None, min_length=1, max_length=64)
    size: str | None = Field(default=None, min_length=1, max_length=32)
    cost_price: _PriceField | None = None
    purchase_price: _PriceField | None = None
    base_price: _PriceField | None = None
    sourcing_type: SourcingType | None = None
    is_active: bool | None = None


class SkuResponse(BaseModel):
    """SKU 响应。

    敏感字段（``cost_price`` / ``purchase_price``）按角色硬编码过滤
    （详见 ``service.SkuService.to_response``）。
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    style_id: UUID
    sku_code: str
    color: str
    size: str
    # 价格字段：service 层按 PRICE_VISIBLE_ROLES 过滤后传入
    cost_price: Decimal | None = None
    purchase_price: Decimal | None = None
    base_price: Decimal | None = None
    sourcing_type: str
    is_active: bool
    is_deleted: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Match（款号 ↔ 商品简称双向关联）
# ---------------------------------------------------------------------------


class MatchCandidate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    style_code: str
    style_name: str
    short_name: str | None = None
    display_short_name: str
    """业务显示用：``short_name`` 优先，否则回退 ``style_name``。"""


class MatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    matched: bool
    """是否匹配到至少一个候选。业务未匹配 -> False；不代表系统错误。"""
    candidates: list[MatchCandidate] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class StylePage(BaseModel):
    items: list[StyleResponse]
    total: int
    page: int
    page_size: int


__all__ = [
    "MatchCandidate",
    "MatchResponse",
    "SkuCreate",
    "SkuResponse",
    "SkuUpdate",
    "StyleCreate",
    "StylePage",
    "StyleResponse",
    "StyleUpdate",
]
