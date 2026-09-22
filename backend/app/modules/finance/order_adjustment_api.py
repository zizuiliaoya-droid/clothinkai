"""U16 拍单/刷单 + 余额流水 API。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Query, Response, UploadFile, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.finance.deps import (
    BalanceServiceDep,
    OrderAdjustmentServiceDep,
)
from app.modules.finance.order_adjustment_schemas import (
    BalanceRecordCreate,
    BalanceRecordResponse,
    BrushingCreate,
    OrderAdjustmentListFilters,
    OrderAdjustmentPage,
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
    response_model=OrderAdjustmentPage,
    dependencies=[require_permission("finance.order", "read")],
)
async def list_order_adjustments(
    user: CurrentActiveUser,
    service: OrderAdjustmentServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    order_type: Annotated[str | None, Query()] = None,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    keyword: Annotated[str | None, Query(max_length=64)] = None,
    style_id: UUID | None = None,
    order_date_from: Annotated[date | None, Query()] = None,
    order_date_to: Annotated[date | None, Query()] = None,
    amount_min: Annotated[Decimal | None, Query(ge=0)] = None,
    amount_max: Annotated[Decimal | None, Query(ge=0)] = None,
    exclude_from_roi: bool | None = None,
    has_payment_qr: bool | None = None,
) -> OrderAdjustmentPage:
    """拍单 / 刷单合并列表。不传 ``order_type`` 则两种单据一起返回。"""
    filters = OrderAdjustmentListFilters(
        order_type=order_type,
        status=status_filter,
        keyword=keyword,
        style_id=style_id,
        order_date_from=order_date_from,
        order_date_to=order_date_to,
        amount_min=amount_min,
        amount_max=amount_max,
        exclude_from_roi=exclude_from_roi,
        has_payment_qr=has_payment_qr,
    )
    return await service.list(filters=filters, page=page, page_size=page_size)


@router.post(
    "/order-adjustments/{row_id}/payment-qr/upload",
    response_model=OrderAdjustmentResponse,
    dependencies=[require_permission("finance.order", "write")],
)
async def upload_order_payment_qr(
    row_id: UUID,
    user: CurrentActiveUser,
    service: OrderAdjustmentServiceDep,
    image: Annotated[UploadFile, File(description="收款码图片（JPG/PNG/WebP，≤10MB）")],
) -> OrderAdjustmentResponse:
    """上传博主收款码。由后端代传到私有桶，避免浏览器直传依赖 bucket CORS。"""
    try:
        data = await image.read()
    finally:
        await image.close()
    return await service.upload_payment_qr(
        row_id,
        filename=image.filename,
        mime_type=image.content_type,
        data=data,
        user=user,
    )


@router.delete(
    "/order-adjustments/{row_id}/payment-qr",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("finance.order", "write")],
)
async def remove_order_payment_qr(
    row_id: UUID,
    user: CurrentActiveUser,
    service: OrderAdjustmentServiceDep,
) -> Response:
    await service.remove_payment_qr(row_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
