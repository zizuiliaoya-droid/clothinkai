"""可维护字典项（DictItem）：类目 / 季节(系列) / 颜色 / 尺码 等由租户自行增删维护的值。

替代原先硬编码的 Category / Season 枚举，支持用户在 UI 上自定义（如 "2026春"、"2027冬"）。
继承 TenantScopedModel（自带 tenant_id + RLS）。
"""

from __future__ import annotations

from sqlalchemy import Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class DictItem(TenantScopedModel):
    """通用字典项。

    ``dict_type`` 取值：category（类目）/ season（季节·系列）/ color（颜色）/ size（尺码）。
    ``(tenant_id, dict_type, value)`` 唯一。
    """

    __tablename__ = "dict_item"

    dict_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        Index(
            "uq_dict_item",
            "tenant_id",
            "dict_type",
            "value",
            unique=True,
        ),
        Index("idx_dict_item_type", "tenant_id", "dict_type", "is_active"),
    )


# 8b-3：存在 dict_item 里、但由别的模块自己的接口和权限维护的类型。商品字典接口
# （product/dict_api.py，挂 product:read/write）看不到、加不了、删不掉它们
RESERVED_DICT_TYPES: frozenset[str] = frozenset({"blogger_tag"})


__all__ = ["RESERVED_DICT_TYPES", "DictItem"]
