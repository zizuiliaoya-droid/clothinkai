"""U02 模块枚举定义。

按 functional-design/domain-entities.md §4：
- Category：商品大类（女装），MVP 硬编码，U09 改字典表
- Season、Gender：硬编码
- DesignStatus：U02 仅 2 值（设计中 / 大货），U10a 扩为 7 值
- SourcingType：SKU 来源类型
"""

from __future__ import annotations

from enum import Enum


class Category(str, Enum):
    """商品大类（女装），U09 阶段改为字典表。"""

    DRESS = "连衣裙"
    TOP = "上衣"
    PANTS = "裤装"
    SKIRT = "裙装"
    OUTERWEAR = "外套"
    SET = "套装"
    ACCESSORY = "配饰"


class Season(str, Enum):
    """季节。"""

    SPRING = "春"
    SUMMER = "夏"
    AUTUMN = "秋"
    WINTER = "冬"
    ALL = "四季"


class Gender(str, Enum):
    """性别。"""

    FEMALE = "女"
    MALE = "男"
    UNISEX = "中性"
    KIDS = "童"


class DesignStatus(str, Enum):
    """设计状态。

    U02 仅 2 值（MVP 简化），U10a 阶段扩为 7 状态状态机：
    设计中 / 打版中 / 工艺中 / 核价中 / 打样中 / 确认中 / 大货
    """

    DESIGNING = "设计中"
    BULK = "大货"


class SourcingType(str, Enum):
    """SKU 来源类型。

    决定 cost_price / purchase_price 的使用：
    - SELF_PRODUCED：自产 → 用 cost_price
    - EXTERNAL_PURCHASE：外采 → 用 purchase_price
    - MIXED：混合 → 两者都可填
    """

    SELF_PRODUCED = "自产"
    EXTERNAL_PURCHASE = "外采"
    MIXED = "混合"
