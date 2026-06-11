"""U17 套装/组合商品 API（/api/bundles）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.modules.auth.deps import CurrentActiveUser, require_permission
from app.modules.product.bundle_schemas import (
    BundleCreate,
    BundleItemResponse,
    BundleResponse,
)
from app.modules.product.deps import BundleServiceDep

router = APIRouter(prefix="/api/bundles", tags=["product"])


def _to_response(bundle, items) -> BundleResponse:
    return BundleResponse(
        id=bundle.id,
        bundle_code=bundle.bundle_code,
        bundle_name=bundle.bundle_name,
        remark=bundle.remark,
        is_active=bundle.is_active,
        items=[
            BundleItemResponse(sku_id=it.sku_id, quantity=it.quantity)
            for it in items
        ],
    )


@router.post(
    "/",
    response_model=BundleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("product.bundle", "write")],
)
async def create_bundle(
    payload: BundleCreate,
    user: CurrentActiveUser,
    service: BundleServiceDep,
) -> BundleResponse:
    bundle = await service.create(payload, user)
    _, items = await service.get_with_items(bundle.id)
    return _to_response(bundle, items)


@router.get(
    "/",
    response_model=list[BundleResponse],
    dependencies=[require_permission("product.bundle", "read")],
)
async def list_bundles(
    user: CurrentActiveUser,
    service: BundleServiceDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[BundleResponse]:
    bundles = await service.list_bundles(limit=limit, offset=offset)
    out = []
    for b in bundles:
        _, items = await service.get_with_items(b.id)
        out.append(_to_response(b, items))
    return out


@router.get(
    "/{bundle_id}",
    response_model=BundleResponse,
    dependencies=[require_permission("product.bundle", "read")],
)
async def get_bundle(
    bundle_id: UUID,
    user: CurrentActiveUser,
    service: BundleServiceDep,
) -> BundleResponse:
    bundle, items = await service.get_with_items(bundle_id)
    return _to_response(bundle, items)


__all__ = ["router"]
