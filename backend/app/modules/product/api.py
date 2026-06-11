"""U02 product 模块 REST API 路由。

按 business-logic-model.md 9 个 UC 实现 13+ 个端点。
全部端点：
- 应用 ``require_permission("product:read|write|delete")`` / ``brand:*``
- 通过 deps 注入 service
- 抛出业务异常 → 全局 error handler 自动映射到 JSON 响应

降级语义（match 接口）：
- 业务未匹配 → 200 + 空候选（service 层处理）
- 系统失败（DB 异常 / 超时）→ 异常自然冒泡 → 5xx + Sentry
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import Response

from app.core.exceptions import ValidationError
from app.modules.auth.deps import (
    CurrentActiveUser,
    require_permission,
)
from app.modules.product.brand_schemas import (
    BrandCreate,
    BrandResponse,
    BrandUpdate,
)
from app.modules.product.deps import (
    BrandServiceDep,
    SkuServiceDep,
    StyleServiceDep,
)
from app.modules.product.repository import StyleListFilters
from app.modules.product.schemas import (
    MatchResponse,
    SkuCreate,
    SkuResponse,
    SkuUpdate,
    StyleCreate,
    StylePage,
    StyleResponse,
    StyleUpdate,
)

router = APIRouter(prefix="/api", tags=["product"])


# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------


@router.post(
    "/styles/",
    response_model=StyleResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("product", "write")],
)
async def create_style(
    payload: StyleCreate,
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> StyleResponse:
    """EP02-S01 跟单创建款式."""
    return await service.create_style(payload, user)


@router.get(
    "/styles/match",
    response_model=MatchResponse,
    dependencies=[require_permission("product", "read")],
)
async def match_styles(
    user: CurrentActiveUser,  # noqa: ARG001
    service: StyleServiceDep,
    style_code: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    keyword: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> MatchResponse:
    """EP02-S06 款号 ↔ 商品简称双向关联.

    业务未匹配返回 200 + 空候选（前端允许用户继续手动输入）；
    系统失败让异常自然冒泡到 5xx。
    """
    if style_code is None and keyword is None:
        raise ValidationError("style_code 或 keyword 至少一个必填")
    if style_code is not None and keyword is not None:
        raise ValidationError("style_code 和 keyword 不能同时传")

    if style_code is not None:
        return await service.match_by_code(style_code)
    assert keyword is not None
    return await service.match_by_keyword(keyword)


@router.get(
    "/styles/",
    response_model=StylePage,
    dependencies=[require_permission("product", "read")],
)
async def list_styles(
    user: CurrentActiveUser,
    service: StyleServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    keyword: Annotated[str | None, Query(max_length=128)] = None,
    brand_id: UUID | None = None,
    category: Annotated[str | None, Query(max_length=32)] = None,
    season: Annotated[str | None, Query(max_length=16)] = None,
    gender: Annotated[str | None, Query(max_length=8)] = None,
    design_status: Annotated[str | None, Query(max_length=16)] = None,
    is_active: bool = True,
    include_inactive: bool = False,
) -> StylePage:
    """款式列表（分页 + 筛选 + ILIKE 关键字搜索）."""
    filters = StyleListFilters(
        keyword=keyword,
        brand_id=brand_id,
        category=category,
        season=season,
        gender=gender,
        design_status=design_status,
        is_active=is_active,
        include_inactive=include_inactive,
    )
    return await service.list_styles(
        filters=filters, page=page, page_size=page_size, user=user
    )


@router.get(
    "/styles/{style_id}",
    response_model=StyleResponse,
    dependencies=[require_permission("product", "read")],
)
async def get_style(
    style_id: UUID,
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> StyleResponse:
    return await service.get_style(style_id, user)


@router.put(
    "/styles/{style_id}",
    response_model=StyleResponse,
    dependencies=[require_permission("product", "write")],
)
async def update_style(
    style_id: UUID,
    payload: StyleUpdate,
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> StyleResponse:
    """EP02-S03 编辑款式."""
    return await service.update_style(style_id, payload, user)


@router.delete(
    "/styles/{style_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("product", "delete")],
)
async def delete_style(
    style_id: UUID,
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> Response:
    await service.soft_delete_style(style_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/styles/{style_id}/disable",
    response_model=StyleResponse,
    dependencies=[require_permission("product", "write")],
)
async def disable_style(
    style_id: UUID,
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> StyleResponse:
    return await service.disable_style(style_id, user)


@router.post(
    "/styles/{style_id}/restore",
    response_model=StyleResponse,
    dependencies=[require_permission("product", "delete")],
)
async def restore_style(
    style_id: UUID,
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> StyleResponse:
    """BR-U02-22 恢复软删的款式."""
    return await service.restore_style(style_id, user)


# ---------------------------------------------------------------------------
# Sku
# ---------------------------------------------------------------------------


@router.post(
    "/skus/",
    response_model=SkuResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("product", "write")],
)
async def create_sku(
    payload: SkuCreate,
    user: CurrentActiveUser,
    service: SkuServiceDep,
) -> SkuResponse:
    """EP02-S02 跟单创建 SKU."""
    return await service.create_sku(payload, user)


@router.get(
    "/skus/by-style/{style_id}",
    response_model=list[SkuResponse],
    dependencies=[require_permission("product", "read")],
)
async def list_skus_by_style(
    style_id: UUID,
    user: CurrentActiveUser,
    service: SkuServiceDep,
    include_inactive: bool = False,
) -> list[SkuResponse]:
    """EP02-S05 按款式查询 SKU."""
    return await service.list_by_style(
        style_id, include_inactive=include_inactive, user=user
    )


@router.get(
    "/skus/{sku_id}",
    response_model=SkuResponse,
    dependencies=[require_permission("product", "read")],
)
async def get_sku(
    sku_id: UUID,
    user: CurrentActiveUser,
    service: SkuServiceDep,
) -> SkuResponse:
    return await service.get_sku(sku_id, user)


@router.put(
    "/skus/{sku_id}",
    response_model=SkuResponse,
    dependencies=[require_permission("product", "write")],
)
async def update_sku(
    sku_id: UUID,
    payload: SkuUpdate,
    user: CurrentActiveUser,
    service: SkuServiceDep,
) -> SkuResponse:
    """EP02-S04 编辑 SKU 成本/价格."""
    return await service.update_sku(sku_id, payload, user)


@router.delete(
    "/skus/{sku_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("product", "delete")],
)
async def delete_sku(
    sku_id: UUID,
    user: CurrentActiveUser,
    service: SkuServiceDep,
) -> Response:
    await service.soft_delete_sku(sku_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------


@router.post(
    "/brands/",
    response_model=BrandResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission("brand", "write")],
)
async def create_brand(
    payload: BrandCreate,
    user: CurrentActiveUser,  # noqa: ARG001
    service: BrandServiceDep,
) -> BrandResponse:
    return await service.create_brand(payload)


@router.get(
    "/brands/",
    response_model=dict,
    dependencies=[require_permission("brand", "read")],
)
async def list_brands(
    user: CurrentActiveUser,  # noqa: ARG001
    service: BrandServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    is_active: bool | None = None,
) -> dict:
    items, total = await service.list_brands(
        is_active=is_active, page=page, page_size=page_size
    )
    return {
        "items": [b.model_dump(mode="json") for b in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get(
    "/brands/{brand_id}",
    response_model=BrandResponse,
    dependencies=[require_permission("brand", "read")],
)
async def get_brand(
    brand_id: UUID,
    user: CurrentActiveUser,  # noqa: ARG001
    service: BrandServiceDep,
) -> BrandResponse:
    return await service.get_brand(brand_id)


@router.put(
    "/brands/{brand_id}",
    response_model=BrandResponse,
    dependencies=[require_permission("brand", "write")],
)
async def update_brand(
    brand_id: UUID,
    payload: BrandUpdate,
    user: CurrentActiveUser,  # noqa: ARG001
    service: BrandServiceDep,
) -> BrandResponse:
    return await service.update_brand(brand_id, payload)


@router.delete(
    "/brands/{brand_id}",
    response_model=BrandResponse,
    dependencies=[require_permission("brand", "delete")],
)
async def disable_brand(
    brand_id: UUID,
    user: CurrentActiveUser,  # noqa: ARG001
    service: BrandServiceDep,
) -> BrandResponse:
    """BR-U02-... 软停用品牌（不硬删）."""
    return await service.disable_brand(brand_id)


__all__ = ["router"]
