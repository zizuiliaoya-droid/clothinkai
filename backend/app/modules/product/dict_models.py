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
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )

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


__all__ = ["DictItem"]
