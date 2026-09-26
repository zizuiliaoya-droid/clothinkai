"""U10b 平台商品映射 API（/api/platform-products）—— 运维视图专用。

「平台链接」是店铺里一条实际在卖的链接（千牛商品ID / 万相台主体ID），业务人员不需要
关心，所以这组端点走 ``ops.platform_link`` 权限而不是 ``product.platform``。

换 scope 的原因：``EffectivePermissions.has`` 的前缀通配只看 scope 第一段，
``product.platform`` 会被跟单/运营持有的 ``product.*:*`` / ``product.*:read`` 命中，
根本挡不住人。换成 ``ops.`` 开头后只有显式授权的角色（以及持 ``*`` 的管理员）能进。
"""

from __future__ import annotations

from typing import Annotated
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

SCOPE = "ops.platform_link"


def _svc(session: SessionDep) -> PlatformProductService:
    return PlatformProductService(session)


@router.post(
    "/",
    response_model=PlatformProductResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(SCOPE, "write")],
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
    dependencies=[require_permission(SCOPE, "read")],
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
    dependencies=[require_permission(SCOPE, "read")],
)
async def list_platform_products(
    session: SessionDep,
    user: CurrentActiveUser,
    style_id: UUID | None = None,
    goods_main_id: UUID | None = None,
    platform: Annotated[str | None, Query(max_length=16)] = None,
    channel: Annotated[str | None, Query(pattern="^(普通|直播)$")] = None,
    keyword: Annotated[
        str | None, Query(max_length=64, description="平台ID / 货号 / 款名 / 商品编码")
    ] = None,
    unmapped_only: Annotated[bool, Query(description="只看还没归到商品的链接")] = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlatformProductListResponse:
    items, total = await _svc(session).list_detailed(
        tenant_id=user.tenant_id,
        style_id=style_id,
        goods_main_id=goods_main_id,
        platform=platform,
        channel=channel,
        keyword=keyword,
        unmapped_only=unmapped_only,
        page=page,
        page_size=page_size,
    )
    return PlatformProductListResponse(items=items, total=total, page=page, page_size=page_size)


@router.put(
    "/{pp_id}",
    response_model=PlatformProductResponse,
    dependencies=[require_permission(SCOPE, "write")],
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
    dependencies=[require_permission(SCOPE, "write")],
)
async def delete_platform_product(
    pp_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
) -> dict[str, bool]:
    await _svc(session).delete(pp_id, user.id)
    return {"ok": True}


__all__ = ["router"]
