"""商品（goods_main）对外 schema。

商品是报表归属的主体（1b 起投产报表按商品聚合）、平台链接的归属对象（1c-1），
这里是它的管理入口。款式仍是货品主数据，商品只引用款式，不复制款式字段。

套装与单品共用一张表，``is_suit`` 区分：单品恰好 1 个成员款式，套装 ≥2 个。
成员行带 ``single_goods_cost`` —— 同一件衣服进不同套装可以按不同成本核算。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GoodsOption(BaseModel):
    """商品候选项：够前端做下拉选择与展示套装标记。"""

    model_config = ConfigDict(from_attributes=True)

    goods_main_id: UUID
    goods_code: str
    goods_title: str
    is_suit: bool = False


# ---------------------------------------------------------------------------
# 成员款式
# ---------------------------------------------------------------------------


class GoodsStyleItemIn(BaseModel):
    """成员款式入参。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    style_id: UUID
    single_goods_cost: Decimal | None = Field(default=None, ge=0, decimal_places=2)
    """该款在该商品下的单件货品成本。不传则建档时从款式的 SKU 成本价取。"""

    sort_order: int = 0


class GoodsStyleItemResponse(BaseModel):
    """成员款式出参，带款式信息省掉前端二次查询。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    style_id: UUID
    style_code: str | None = None
    style_name: str | None = None
    single_goods_cost: Decimal | None = None
    sort_order: int = 0
    is_active: bool = True


# ---------------------------------------------------------------------------
# 商品
# ---------------------------------------------------------------------------


class GoodsMainCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    goods_code: str = Field(..., min_length=1, max_length=64)
    goods_title: str = Field(..., min_length=1, max_length=512)
    category: str | None = Field(default=None, max_length=64)
    season: str | None = Field(default=None, max_length=64)
    brand_id: UUID | None = None
    main_image_key: str | None = Field(default=None, max_length=512)
    remark: str | None = None

    items: list[GoodsStyleItemIn] = Field(default_factory=list)
    """成员款式。给 0 个会被拒 —— 没有款式的商品既发不了货也算不出成本。
    ``is_suit`` 不由前端传，服务端按成员数判定（≥2 即套装），避免两个字段互相矛盾。"""


class GoodsMainUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    goods_title: str | None = Field(default=None, min_length=1, max_length=512)
    category: str | None = Field(default=None, max_length=64)
    season: str | None = Field(default=None, max_length=64)
    brand_id: UUID | None = None
    main_image_key: str | None = Field(default=None, max_length=512)
    remark: str | None = None
    is_active: bool | None = None

    items: list[GoodsStyleItemIn] | None = None
    """给了就整体替换成员列表（含成本与排序），不给则不动。

    ``goods_code`` 不可改：它是报表与链接归属的引用键，改了等于换了一个商品。
    """


class GoodsMainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    goods_code: str
    goods_title: str
    category: str | None = None
    season: str | None = None
    brand_id: UUID | None = None
    brand_name: str | None = None
    main_image_key: str | None = None
    remark: str | None = None
    is_suit: bool = False
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    items: list[GoodsStyleItemResponse] = Field(default_factory=list)
    total_cost: Decimal | None = None
    """成员成本之和（只算启用的成员行）。套装成本就是各件衣服成本相加。
    任一成员没填成本时仍按已填的求和，前端用 ``cost_missing_count`` 提示不完整。"""

    cost_missing_count: int = 0
    link_count: int = 0
    """挂在该商品上的平台链接数。为 0 说明商品还没上架到任何渠道。"""


class GoodsMainListResponse(BaseModel):
    items: list[GoodsMainResponse]
    total: int
    page: int = 1
    page_size: int = 20


__all__ = [
    "GoodsMainCreate",
    "GoodsMainListResponse",
    "GoodsMainResponse",
    "GoodsMainUpdate",
    "GoodsOption",
    "GoodsStyleItemIn",
    "GoodsStyleItemResponse",
]
