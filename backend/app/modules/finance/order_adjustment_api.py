"""U16 拍单/刷单 + 余额流水 API。"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.finance.deps import (
    BalanceServiceDep,
    OrderAdjustmentServiceDep,
)
from app.modules.finance.order_adjustment_schemas import (
    BalanceRecordCreate,
    BalanceRecordResponse,
    BrushingCreate,
    OrderAdjustmentResponse,
)

router = APIRouter(prefix="/api/finance", tags=["finance"])


# ----------------------------- 刷单 / 拍单 ----------------------------- #


@router.post(
    "/order-adjustments/brushing",
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("finance.order", "write")],
)
async def create_brushing(
    payload: BrushingCreate,
    user: CurrentActiveUser,
    service: OrderAdjustmentServiceDep,
) -> dict:
    return await service.create_brushing(payload, user)


@router.get(
    "/order-adjustments",
    response_model=list[OrderAdjustmentResponse],
    dependencies=[require_permission("finance.order", "read")],
)
async def list_order_adjustments(
    user: CurrentActiveUser,
    service: OrderAdjustmentServiceDep,
    order_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[OrderAdjustmentResponse]:
    rows = await service.list(order_type=order_type, limit=limit, offset=offset)
    return [OrderAdjustmentResponse.model_validate(r, from_attributes=True) for r in rows]


# ----------------------------- 余额流水 ----------------------------- #


@router.post(
    "/balance-records",
    response_model=BalanceRecordResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("finance.balance", "write")],
)
async def add_balance_record(
    payload: BalanceRecordCreate,
    user: CurrentActiveUser,
    service: BalanceServiceDep,
) -> BalanceRecordResponse:
    row = await service.add_record(payload, user)
    return BalanceRecordResponse.model_validate(row, from_attributes=True)


@router.get(
    "/balance-records",
    response_model=list[BalanceRecordResponse],
    dependencies=[require_permission("finance.balance", "read")],
)
async def list_balance_records(
    user: CurrentActiveUser,
    service: BalanceServiceDep,
    date_from: Annotated[date | None, Query()] = None,
    date_to: Annotated[date | None, Query()] = None,
) -> list[BalanceRecordResponse]:
    rows = await service.list(date_from=date_from, date_to=date_to)
    return [BalanceRecordResponse.model_validate(r, from_attributes=True) for r in rows]


__all__ = ["router"]
