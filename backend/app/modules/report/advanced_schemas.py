"""U14 报表进阶读模型 + 写入 Schema。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ----------------------------- 工作进度 ----------------------------- #


class PrWorkProgress(BaseModel):
    pr_id: UUID | None = None
    pr_name: str
    quote_count: int
    in_schedule_count: int
    urge_count: int
    important_urge_count: int
    overdue_count: int
    publish_count: int
    info_complete_rate: Decimal | None = None
    cancel_count: int
    recall_due_count: int
    recall_success_count: int
    recall_complete_rate: Decimal | None = None
    overdue_rate: Decimal | None = None
    month_complete_rate: Decimal | None = None
    hit_count: int
    hit_rate: Decimal | None = None
    like_count: int
    cost: Decimal
    cpl: Decimal | None = None


# ----------------------------- 爆款约篇 ----------------------------- #


class TargetCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    pr_id: UUID
    style_id: UUID
    period_month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    min_target: int = Field(..., ge=0)


class TargetWithActual(BaseModel):
    id: UUID
    pr_id: UUID
    pr_name: str
    style_id: UUID
    style_code: str
    style_name: str
    period_month: str
    min_target: int
    actual_count: int
    status: str
    gap: int


# ----------------------------- 店铺数据 ----------------------------- #


class StoreDailyRow(BaseModel):
    date: date
    visitors: int
    pay_amount: Decimal
    pay_orders: int
    ad_spend_total: Decimal | None = None
    zhitongche_spend: Decimal | None = None
    yinli_spend: Decimal | None = None
    # 千牛日报按日汇总的其余指标（对齐 final.xlsx 店铺数据；SUM qianniu_daily.extra 数值列）
    extra: dict = Field(default_factory=dict)


class StoreDailyManualUpdate(BaseModel):
    ad_spend_total: Decimal | None = None
    zhitongche_spend: Decimal | None = None
    yinli_spend: Decimal | None = None
    remark: str | None = None


# ----------------------------- 投产报表 ----------------------------- #


class ProductionRow(BaseModel):
    style_id: UUID
    style_code: str
    style_name: str
    main_image_url: str | None = None
    pay_amount: Decimal
    refund_amount: Decimal
    return_rate: Decimal | None = None
    confirmed_amount: Decimal
    promo_cost: Decimal
    ad_spend: Decimal
    total_spend: Decimal
    add_cart_count: int
    add_cart_cost: Decimal | None = None
    net_roi: Decimal | None = None
    unit_deal_cost: Decimal | None = None
    # 千牛/站内导入数据按款式汇总的其余指标（对齐 final.xlsx 投产报表 70 列；
    # SUM qianniu_daily.extra + ad_daily.extra 的数值列，按 platform_product→style 归集）
    extra: dict = Field(default_factory=dict)


class ProductionReport(BaseModel):
    items: list[ProductionRow]
    previous: list[ProductionRow] | None = None


class ProductionTrendPoint(BaseModel):
    """单款趋势桶；date 为日值或周/月/年桶的起始日期。"""

    model_config = ConfigDict(from_attributes=True)

    date: date
    pay_amount: Decimal = Decimal("0")
    ad_spend: Decimal = Decimal("0")


class ProductionTrend(BaseModel):
    points: list[ProductionTrendPoint]


# ----------------------------- BI 看板 ----------------------------- #


class BiStoreSummary(BaseModel):
    sales_amount: Decimal
    refund_amount: Decimal
    return_rate: Decimal | None = None
    internal_spend: Decimal
    external_spend: Decimal
    total_spend: Decimal
    internal_spend_ratio: Decimal | None = None
    external_spend_ratio: Decimal | None = None
    roi: Decimal | None = None


class BiPromotionSummary(BaseModel):
    commission_amount: Decimal
    commission_count: int
    published_spend: Decimal
    published_count: int
    unpublished_spend: Decimal
    unpublished_count: int
    cancelled_amount: Decimal
    cancelled_count: int
    publish_rate: Decimal | None = None


class BiWorkloadRow(BaseModel):
    pr_id: UUID | None = None
    pr_name: str
    target_count: int
    quote_count: int
    quote_progress: Decimal | None = None
    publish_count: int
    publish_progress: Decimal | None = None
    pending_count: int
    cancel_count: int
    overdue_count: int


class BiStylePerformance(BaseModel):
    style_id: UUID
    style_code: str
    style_name: str
    main_image_url: str | None = None
    sales_amount: Decimal
    refund_amount: Decimal
    return_rate: Decimal | None = None
    confirmed_amount: Decimal
    internal_spend: Decimal
    external_spend: Decimal
    total_spend: Decimal
    roi: Decimal | None = None


class BiTrendPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    sales_amount: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    internal_spend: Decimal = Decimal("0")
    external_spend: Decimal = Decimal("0")


class BiDashboard(BaseModel):
    date_from: date
    date_to: date
    granularity: str
    store_summary: BiStoreSummary
    promotion_summary: BiPromotionSummary
    workload: list[BiWorkloadRow]
    style_performance: list[BiStylePerformance]
    trend: list[BiTrendPoint]
    # 兼容 U17 旧客户端；新前端只消费上面的结构化字段。
    cards: list[dict] = Field(default_factory=list)
    charts: list[dict] = Field(default_factory=list)


__all__ = [
    "BiDashboard",
    "BiPromotionSummary",
    "BiStoreSummary",
    "BiStylePerformance",
    "BiTrendPoint",
    "BiWorkloadRow",
    "PrWorkProgress",
    "ProductionReport",
    "ProductionRow",
    "ProductionTrend",
    "ProductionTrendPoint",
    "StoreDailyManualUpdate",
    "StoreDailyRow",
    "TargetCreate",
    "TargetWithActual",
]
