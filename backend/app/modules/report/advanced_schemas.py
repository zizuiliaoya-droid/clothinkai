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


__all__ = [
    "PrWorkProgress",
    "ProductionReport",
    "ProductionRow",
    "StoreDailyManualUpdate",
    "StoreDailyRow",
    "TargetCreate",
    "TargetWithActual",
]
