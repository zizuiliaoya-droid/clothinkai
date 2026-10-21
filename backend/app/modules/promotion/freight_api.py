"""召回寄回运费七端点；付款单独立于服务费结款单。"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.promotion.freight_schemas import (
    FreightListFilters,
    FreightRejectRequest,
    FreightResubmitRequest,
    FreightReviewRequest,
    FreightStatus,
    FreightSubmitRequest,
    ReturnFreightPaymentPage,
    ReturnFreightPaymentResponse,
)
from app.modules.promotion.freight_service import ReturnFreightService

router = APIRouter(prefix="/api", tags=["return-freight"])


def get_freight_service(session: SessionDep) -> ReturnFreightService:
    return ReturnFreightService(session)


FreightServiceDep = Annotated[ReturnFreightService, Depends(get_freight_service)]


@router.post(
    "/promotions/{promotion_id}/return-freight",
    response_model=ReturnFreightPaymentResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def submit(
    promotion_id: UUID,
    payload: FreightSubmitRequest,
    user: CurrentActiveUser,
    service: FreightServiceDep,
) -> ReturnFreightPaymentResponse:
    return await service.response(await service.submit(promotion_id, payload, user), user)


@router.get(
    "/return-freight-payments/",
    response_model=ReturnFreightPaymentPage,
    dependencies=[require_permission("finance.settlement", "read")],
)
async def page(
    user: CurrentActiveUser,
    service: FreightServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: FreightStatus | None = None,
    promotion_id: UUID | None = None,
    keyword: Annotated[str | None, Query(max_length=64)] = None,
    payment_date_from: date | None = None,
    payment_date_to: date | None = None,
) -> ReturnFreightPaymentPage:
    filters = FreightListFilters(
        status=status,
        promotion_id=promotion_id,
        keyword=keyword,
        payment_date_from=payment_date_from,
        payment_date_to=payment_date_to,
    )
    return await service.page(filters, page=page, page_size=page_size, user=user)


@router.get("/return-freight-payments/{freight_id}", response_model=ReturnFreightPaymentResponse)
async def get(
    freight_id: UUID, user: CurrentActiveUser, service: FreightServiceDep
) -> ReturnFreightPaymentResponse:
    return await service.get(freight_id, user)


@router.post(
    "/return-freight-payments/{freight_id}/review",
    response_model=ReturnFreightPaymentResponse,
    dependencies=[require_permission("promotion_review", "approve")],
)
async def review(
    freight_id: UUID,
    payload: FreightReviewRequest,
    user: CurrentActiveUser,
    service: FreightServiceDep,
) -> ReturnFreightPaymentResponse:
    return await service.response(await service.review(freight_id, payload, user), user)


@router.post(
    "/return-freight-payments/{freight_id}/pay",
    response_model=ReturnFreightPaymentResponse,
    dependencies=[require_permission("finance.settlement", "pay")],
)
async def pay(
    freight_id: UUID,
    user: CurrentActiveUser,
    service: FreightServiceDep,
    file: Annotated[UploadFile, File()],
) -> ReturnFreightPaymentResponse:
    try:
        data = await file.read(10 * 1024 * 1024 + 1)
        payment = await service.pay(
            freight_id, filename=file.filename, mime_type=file.content_type, data=data, user=user
        )
    finally:
        await file.close()
    return await service.response(payment, user)


@router.post(
    "/return-freight-payments/{freight_id}/reject-payment",
    response_model=ReturnFreightPaymentResponse,
    dependencies=[require_permission("finance.settlement", "pay")],
)
async def reject_payment(
    freight_id: UUID,
    payload: FreightRejectRequest,
    user: CurrentActiveUser,
    service: FreightServiceDep,
) -> ReturnFreightPaymentResponse:
    return await service.response(await service.reject_payment(freight_id, payload, user), user)


@router.post(
    "/return-freight-payments/{freight_id}/resubmit",
    response_model=ReturnFreightPaymentResponse,
    dependencies=[require_permission("promotion", "write")],
)
async def resubmit(
    freight_id: UUID,
    payload: FreightResubmitRequest,
    user: CurrentActiveUser,
    service: FreightServiceDep,
) -> ReturnFreightPaymentResponse:
    return await service.response(await service.resubmit(freight_id, payload, user), user)
