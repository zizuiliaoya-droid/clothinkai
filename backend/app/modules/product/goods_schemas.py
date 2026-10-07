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

from pydantic import BaseModel, ConfigDict, Field, field_validator

GOODS_SHORT_NAME_MAX_LEN = 32
"""商品简称上限。列上留了 64，接口先收紧到 32 —— 简称是拿来显示的，太长就失去意义了。"""


def goods_display_name(goods_title: str, short_name: str | None) -> str:
    """商品在界面 / 消息里显示的名字：有简称用简称，没填回落全称。"""
    return short_name or goods_title


def _blank_to_none(value: str | None) -> str | None:
    # 输入框清空后传上来的是空串；存成 NULL，「没填简称」只有一种表示
    return value or None


class GoodsOption(BaseModel):
    """商品候选项：够前端做下拉选择与展示套装标记。"""

    model_config = ConfigDict(from_attributes=True)

    goods_main_id: UUID
    goods_code: str
    goods_title: str
    goods_short_name: str | None = None
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
    short_name: str | None = Field(default=None, max_length=GOODS_SHORT_NAME_MAX_LEN)
    category: str | None = Field(default=None, max_length=64)
    season: str | None = Field(default=None, max_length=64)
    # 品牌只读（8a-4，A12）：只由商品资料导入写入，接口不再收 brand_id（传了被忽略）
    main_image_key: str | None = Field(default=None, max_length=512)
    remark: str | None = None

    items: list[GoodsStyleItemIn] = Field(default_factory=list)
    """成员款式。给 0 个会被拒 —— 没有款式的商品既发不了货也算不出成本。
    ``is_suit`` 不由前端传，服务端按成员数判定（≥2 即套装），避免两个字段互相矛盾。"""

    normalize_short_name = field_validator("short_name")(_blank_to_none)


class GoodsMainUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    goods_title: str | None = Field(default=None, min_length=1, max_length=512)
    short_name: str | None = Field(default=None, max_length=GOODS_SHORT_NAME_MAX_LEN)
    """不传不动；传 ``null`` 或空串清掉简称（之后回落显示全称）。

    其余可选字段是「传 None 等于没传」，简称不行：清空简称是正常操作，
    所以服务层按 ``model_fields_set`` 判断它有没有被传。
    """
    category: str | None = Field(default=None, max_length=64)
    season: str | None = Field(default=None, max_length=64)
    # 品牌只读（8a-4，A12）：接口不再收 brand_id（传了被忽略）
    main_image_key: str | None = Field(default=None, max_length=512)
    remark: str | None = None
    is_active: bool | None = None

    items: list[GoodsStyleItemIn] | None = None
    """给了就整体替换成员列表（含成本与排序），不给则不动。

    ``goods_code`` 不可改：它是报表与链接归属的引用键，改了等于换了一个商品。
    """

    normalize_short_name = field_validator("short_name")(_blank_to_none)


class GoodsMainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    goods_code: str
    goods_title: str
    short_name: str | None = None
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


class GoodsBrandOption(BaseModel):
    id: UUID
    brand_name: str


class GoodsBrandOptionsResponse(BaseModel):
    """``GET /api/goods/brand-options``：启用品牌（按名称），给成本表的品牌筛选用（J19）。"""

    items: list[GoodsBrandOption]


class GoodsMainListResponse(BaseModel):
    items: list[GoodsMainResponse]
    total: int
    page: int = 1
    page_size: int = 20


__all__ = [
    "GOODS_SHORT_NAME_MAX_LEN",
    "GoodsBrandOption",
    "GoodsBrandOptionsResponse",
    "GoodsMainCreate",
    "GoodsMainListResponse",
    "GoodsMainResponse",
    "GoodsMainUpdate",
    "GoodsOption",
    "GoodsStyleItemIn",
    "GoodsStyleItemResponse",
    "goods_display_name",
]
