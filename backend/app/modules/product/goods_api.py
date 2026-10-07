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
from app.modules.product.brand_repository import BrandRepository
from app.modules.product.goods_repository import GoodsListFilters
from app.modules.product.goods_schemas import (
    GoodsBrandOption,
    GoodsBrandOptionsResponse,
    GoodsMainCreate,
    GoodsMainListResponse,
    GoodsMainResponse,
    GoodsMainUpdate,
    SeasonOptionsResponse,
)
from app.modules.product.goods_service import GoodsService
from app.modules.product.season_options import list_season_options

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


# 固定路径必须声明在 /{goods_id} 之前：路径参数是 UUID，排在前面会把它们解析成 422
@router.get(
    "/brand-options",
    response_model=GoodsBrandOptionsResponse,
    dependencies=[require_permission(SCOPE, "read")],
)
async def list_goods_brand_options(
    session: SessionDep,
    user: CurrentActiveUser,
) -> GoodsBrandOptionsResponse:
    """启用品牌（按名称），给成本表的品牌筛选用（J19）。

    ``/api/brands/`` 只有管理员能读，跟单 / 运营的品牌下拉一直是空的；这里挂商品读权限。
    """
    rows = await BrandRepository(session).list_active_options(user.tenant_id)
    return GoodsBrandOptionsResponse(
        items=[GoodsBrandOption(id=brand_id, brand_name=name) for brand_id, name in rows]
    )


@router.get(
    "/season-options",
    response_model=SeasonOptionsResponse,
    dependencies=[require_permission(SCOPE, "read")],
)
async def list_goods_season_options(
    session: SessionDep,
    user: CurrentActiveUser,
) -> SeasonOptionsResponse:
    """商品页季节筛选与表单的选项：字典 season 启用值 + 商品上出现过的值（8a-3，§8.3）。"""
    return SeasonOptionsResponse(items=await list_season_options(session, user.tenant_id))


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
