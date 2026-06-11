"""U10b 平台商品映射 API（/api/platform-products）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.product.platform_product_schemas import (
    PlatformProductCreate,
    PlatformProductListResponse,
    PlatformProductResponse,
    PlatformProductUpdate,
)
from app.modules.product.platform_product_service import PlatformProductService

router = APIRouter(prefix="/api/platform-products", tags=["product"])

SCOPE_READ = "product.platform"
SCOPE_WRITE = "product.platform"


def _svc(session: SessionDep) -> PlatformProductService:
    return PlatformProductService(session)


@router.post(
    "/",
    response_model=PlatformProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(SCOPE_WRITE, "write")],
)
async def create_platform_product(
    payload: PlatformProductCreate,
    session: SessionDep,
    user: CurrentActiveUser,
) -> PlatformProductResponse:
    return await _svc(session).create(payload, user.id)


@router.get(
    "/lookup",
    response_model=PlatformProductResponse | None,
    dependencies=[require_permission(SCOPE_READ, "read")],
)
async def lookup_platform_product(
    session: SessionDep,
    _user: CurrentActiveUser,
    platform: str = Query(...),
    platform_id: str = Query(...),
) -> PlatformProductResponse | None:
    pp = await _svc(session).find_by_platform_id(platform, platform_id)
    if pp is None:
        return None
    return PlatformProductResponse.model_validate(pp)


@router.get(
    "/",
    response_model=PlatformProductListResponse,
    dependencies=[require_permission(SCOPE_READ, "read")],
)
async def list_platform_products(
    session: SessionDep,
    user: CurrentActiveUser,
    style_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlatformProductListResponse:
    items, total = await _svc(session).list(
        tenant_id=user.tenant_id, style_id=style_id, page=page, page_size=page_size
    )
    return PlatformProductListResponse(
        items=[PlatformProductResponse.model_validate(i) for i in items],
        total=total,
    )


@router.put(
    "/{pp_id}",
    response_model=PlatformProductResponse,
    dependencies=[require_permission(SCOPE_WRITE, "write")],
)
async def update_platform_product(
    pp_id: UUID,
    payload: PlatformProductUpdate,
    session: SessionDep,
    user: CurrentActiveUser,
) -> PlatformProductResponse:
    return await _svc(session).update(pp_id, payload, user.id)


@router.delete(
    "/{pp_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[require_permission(SCOPE_WRITE, "write")],
)
async def delete_platform_product(
    pp_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
) -> dict[str, bool]:
    await _svc(session).delete(pp_id, user.id)
    return {"ok": True}


__all__ = ["router"]
