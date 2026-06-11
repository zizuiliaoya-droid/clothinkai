"""U08 report Pydantic 读模型。"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel


class ProgressSummary(BaseModel):
    """Layer 1 全局汇总（9 指标 + 着色 level）。"""

    quote_count: int
    quote_amount: Decimal
    cooperation_amount: Decimal
    publish_count: int
    publish_rate: Decimal | None = None
    publish_rate_level: str | None = None
    overdue_count: int
    overdue_rate: Decimal | None = None
    overdue_rate_level: str | None = None
    like_count: int
    cpl: Decimal | None = None
    cancel_count: int


class StyleCard(BaseModel):
    """Layer 2 商品卡片。"""

    style_id: UUID
    style_code: str
    style_name: str
    main_image_key: str | None = None
    cost: Decimal
    quote_count: int
    quote_amount: Decimal
    publish_count: int
    cooperation_amount: Decimal
    cancel_count: int
    overdue_count: int
    like_count: int
    cpl: Decimal | None = None
    publish_rate: Decimal | None = None
    overdue_rate: Decimal | None = None


class StyleCardPage(BaseModel):
    items: list[StyleCard]
    total: int
    page: int
    page_size: int


class PrDetail(BaseModel):
    """Layer 3 Tab1 PR 维度明细。"""

    pr_id: UUID | None = None
    pr_name: str
    quote_count: int
    publish_count: int
    overdue_count: int
    like_count: int
    publish_rate: Decimal | None = None


class TimeSeriesPoint(BaseModel):
    """Layer 3 Tab2 半月周期趋势点。"""

    period_label: str
    quote_count: int
    publish_count: int
    like_count: int


__all__ = [
    "PrDetail",
    "ProgressSummary",
    "StyleCard",
    "StyleCardPage",
    "TimeSeriesPoint",
]
