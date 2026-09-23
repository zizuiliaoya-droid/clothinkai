"""U16 拍单/刷单/余额 Schema。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class BrushingCreate(BaseModel):
    """刷单录入（金额支持"原价-返现"表达式）。"""

    order_date: date | None = None
    order_no: str | None = None
    style_id: UUID | None = None
    sku_id: UUID | None = None
    blogger_identifier: str | None = None
    amount_expr: str = Field(..., description="金额或'原价-返现'，如 100-30")
    remark: str | None = None


class OrderAdjustmentResponse(BaseModel):
    id: UUID
    order_type: str
    order_date: date | None = None
    order_no: str | None = None
    style_id: UUID | None = None
    sku_id: UUID | None = None
    style_code: str | None = None
    style_name: str | None = None
    blogger_identifier: str | None = None
    amount: Decimal
    payment_amount: Decimal | None = None
    payment_date: date | None = None
    exclude_from_roi: bool
    status: str
    promotion_id: UUID | None = None
    remark: str | None = None
    payment_qr_attachment_id: UUID | None = None
    payment_qr_signed_url: str | None = None
    """收款码的短时签名 URL（私有桶，附件 ready 时才有值）。"""
    duplicate: bool = False


class OrderAdjustmentListFilters(BaseModel):
    """拍单/刷单列表筛选。合并页面后两种单据同表展示，筛选必须落在服务端。"""

    order_type: str | None = None
    status: str | None = None
    keyword: str | None = Field(default=None, max_length=64)
    """匹配订单号 / 博主ID或微信ID。"""
    style_id: UUID | None = None
    order_date_from: date | None = None
    order_date_to: date | None = None
    amount_min: Decimal | None = Field(default=None, ge=0)
    amount_max: Decimal | None = Field(default=None, ge=0)
    exclude_from_roi: bool | None = None
    has_payment_qr: bool | None = None


class OrderAdjustmentPage(BaseModel):
    items: list[OrderAdjustmentResponse]
    total: int
    page: int
    page_size: int


class BalanceRecordCreate(BaseModel):
    record_date: date
    record_type: str
    income: Decimal | None = Field(None, ge=0)
    expense: Decimal | None = Field(None, ge=0)
    expected_balance: Decimal | None = None
    remark: str | None = None


class BalanceRecordResponse(BaseModel):
    id: UUID
    record_date: date
    record_type: str
    income: Decimal | None = None
    expense: Decimal | None = None
    balance_after: Decimal
    remark: str | None = None


__all__ = [
    "BalanceRecordCreate",
    "BalanceRecordResponse",
    "BrushingCreate",
    "OrderAdjustmentListFilters",
    "OrderAdjustmentPage",
    "OrderAdjustmentResponse",
]
