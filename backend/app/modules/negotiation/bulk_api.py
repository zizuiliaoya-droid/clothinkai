"""批量端点保留每个原动作的权限域，每行结果独立。"""

from fastapi import APIRouter

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.negotiation.bulk_schemas import (
    BulkActionRequest,
    BulkCreateRequest,
    BulkLookupRequest,
    BulkResponse,
)
from app.modules.negotiation.bulk_service import BulkNegotiationService

router = APIRouter(prefix="/bulk")


@router.post(
    "/create",
    response_model=BulkResponse,
    dependencies=[require_permission("negotiation", "write")],
)
async def create(
    request: BulkCreateRequest, session: SessionDep, user: CurrentActiveUser
) -> BulkResponse:
    return await BulkNegotiationService(session).create(request, user)


@router.post(
    "/lookup", response_model=BulkResponse, dependencies=[require_permission("negotiation", "read")]
)
async def lookup(
    request: BulkLookupRequest, session: SessionDep, user: CurrentActiveUser
) -> BulkResponse:
    return await BulkNegotiationService(session).lookup(request, user)


@router.post(
    "/submit",
    response_model=BulkResponse,
    dependencies=[require_permission("negotiation", "write")],
)
async def submit(
    request: BulkActionRequest, session: SessionDep, user: CurrentActiveUser
) -> BulkResponse:
    return await BulkNegotiationService(session).perform("submit", request, user)


@router.post(
    "/review",
    response_model=BulkResponse,
    dependencies=[require_permission("negotiation.review", "approve")],
)
async def review(
    request: BulkActionRequest, session: SessionDep, user: CurrentActiveUser
) -> BulkResponse:
    return await BulkNegotiationService(session).perform("review", request, user)


@router.post(
    "/final-review",
    response_model=BulkResponse,
    dependencies=[require_permission("negotiation_final", "approve")],
)
async def final_review(
    request: BulkActionRequest, session: SessionDep, user: CurrentActiveUser
) -> BulkResponse:
    return await BulkNegotiationService(session).perform("final-review", request, user)


@router.post(
    "/confirm-receiver",
    response_model=BulkResponse,
    dependencies=[require_permission("negotiation", "write")],
)
async def confirm_receiver(
    request: BulkActionRequest, session: SessionDep, user: CurrentActiveUser
) -> BulkResponse:
    return await BulkNegotiationService(session).perform("confirm-receiver", request, user)
