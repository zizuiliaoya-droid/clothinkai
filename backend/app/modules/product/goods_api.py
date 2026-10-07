"""商品 / 套装管理 API（/api/goods）。

权限走 ``product.goods`` —— 刻意留在 ``product.*`` 下，让跟单与运营（8a-7 起）的
``product.*:*`` 自然可写、设计的 ``product.*:read`` 自然只读。这与平台链接（``ops.platform_link``）
相反：那组必须躲开 ``product.*`` 才挡得住业务角色，而商品本来就是产品主数据。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.modules.auth.deps import CurrentActiveUser, SessionDep, require_permission
from app.modules.product.goods_repository import GoodsListFilters
from app.modules.product.goods_schemas import (
    GoodsMainCreate,
    GoodsMainListResponse,
    GoodsMainResponse,
    GoodsMainUpdate,
)
from app.modules.product.goods_service import GoodsService

router = APIRouter(prefix="/api/goods", tags=["product"])

SCOPE = "product.goods"


def _svc(session: SessionDep) -> GoodsService:
    return GoodsService(session)


@router.post(
    "/",
    response_model=GoodsMainResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(SCOPE, "write")],
)
async def create_goods(
    payload: GoodsMainCreate,
    session: SessionDep,
    user: CurrentActiveUser,
) -> GoodsMainResponse:
    return await _svc(session).create(payload, tenant_id=user.tenant_id, user_id=user.id)


@router.get(
    "/",
    response_model=GoodsMainListResponse,
    dependencies=[require_permission(SCOPE, "read")],
)
async def list_goods(
    session: SessionDep,
    user: CurrentActiveUser,
    keyword: Annotated[
        str | None, Query(max_length=64, description="商品编码 / 商品名 / 成员货号 / 款名")
    ] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    season: Annotated[str | None, Query(max_length=64)] = None,
    brand_id: UUID | None = None,
    is_suit: bool | None = None,
    is_active: bool | None = None,
    include_inactive: bool = False,
    unlinked_only: Annotated[bool, Query(description="只看还没挂平台链接的商品")] = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> GoodsMainListResponse:
    items, total = await _svc(session).list_goods(
        tenant_id=user.tenant_id,
        filters=GoodsListFilters(
            keyword=keyword,
            category=category,
            season=season,
            brand_id=brand_id,
            is_suit=is_suit,
            is_active=is_active,
            include_inactive=include_inactive,
            unlinked_only=unlinked_only,
        ),
        page=page,
        page_size=page_size,
    )
    return GoodsMainListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/{goods_id}",
    response_model=GoodsMainResponse,
    dependencies=[require_permission(SCOPE, "read")],
)
async def get_goods(
    goods_id: UUID,
    session: SessionDep,
    _user: CurrentActiveUser,
) -> GoodsMainResponse:
    return await _svc(session).get(goods_id)


@router.put(
    "/{goods_id}",
    response_model=GoodsMainResponse,
    dependencies=[require_permission(SCOPE, "write")],
)
async def update_goods(
    goods_id: UUID,
    payload: GoodsMainUpdate,
    session: SessionDep,
    user: CurrentActiveUser,
) -> GoodsMainResponse:
    return await _svc(session).update(goods_id, payload, user_id=user.id)


@router.delete(
    "/{goods_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[require_permission(SCOPE, "write")],
)
async def delete_goods(
    goods_id: UUID,
    session: SessionDep,
    user: CurrentActiveUser,
) -> dict[str, bool]:
    """软删商品并留痕。仍挂着平台链接时返回 409 —— 先把链接改到别的商品上。"""
    await _svc(session).soft_delete(goods_id, user_id=user.id)
    return {"ok": True}


__all__ = ["router"]
