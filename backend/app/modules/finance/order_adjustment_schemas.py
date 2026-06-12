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
    exclude_from_roi: bool
    status: str
    promotion_id: UUID | None = None
    remark: str | None = None
    duplicate: bool = False


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
    "OrderAdjustmentResponse",
]
