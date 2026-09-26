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

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import text

from app.core.exceptions import ValidationError
from app.modules.auth.deps import (
    CurrentActiveUser,
    SessionDep,
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
from app.modules.product.goods_schemas import GoodsOption
from app.modules.product.repository import StyleListFilters
from app.modules.product.schemas import (
    CostTablePage,
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
    user: CurrentActiveUser,
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
    category: Annotated[str | None, Query(max_length=64)] = None,
    season: Annotated[str | None, Query(max_length=64)] = None,
    gender: Annotated[str | None, Query(max_length=8)] = None,
    design_status: Annotated[str | None, Query(max_length=16)] = None,
    is_active: bool | None = None,
    include_inactive: bool = False,
) -> StylePage:
    """款式列表（分页 + 筛选 + ILIKE 关键字搜索）.

    状态筛选三态：
    - 都不传（默认）→ 只看启用，与历史行为一致
    - ``is_active=false`` → 只看停用
    - ``include_inactive=true`` 且不传 ``is_active`` → 启用 + 停用都看（停用排在最后）
    """
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
    return await service.list_styles(filters=filters, page=page, page_size=page_size, user=user)


@router.get(
    "/styles/{style_id}/goods",
    response_model=list[GoodsOption],
    dependencies=[require_permission("product", "read")],
)
async def list_goods_for_style(
    user: CurrentActiveUser,
    session: SessionDep,
    style_id: UUID,
) -> list[GoodsOption]:
    """款式归属的商品（单品 / 套装），非套装优先、货号次之。

    录推广时用来确认「这笔推广是为哪个商品做的」。返回多条说明该款式既单卖又进套装，
    需要人工指定；只有一条时前端直接用它，不打扰用户。
    """
    sql = text(
        """
        SELECT g.id AS goods_main_id, g.goods_code, g.goods_title, g.is_suit
        FROM goods_style_item gi
        JOIN goods_main g ON g.id = gi.goods_main_id
        WHERE gi.tenant_id = :tenant_id
          AND gi.style_id = :style_id
          AND gi.is_active = true
          AND g.is_deleted = false
        ORDER BY g.is_suit, g.goods_code
        """
    )
    rows = (
        await session.execute(sql, {"tenant_id": user.tenant_id, "style_id": style_id})
    ).mappings()
    return [GoodsOption.model_validate(dict(row)) for row in rows]


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


@router.post(
    "/styles/{style_id}/main-image",
    response_model=StyleResponse,
    dependencies=[require_permission("product", "write")],
)
async def upload_style_main_image(
    style_id: UUID,
    image: Annotated[UploadFile, File(...)],
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> StyleResponse:
    """上传单张款式主图；压缩后文件必须严格小于 300KB。"""
    try:
        data = await image.read(300 * 1024 + 1)
    finally:
        await image.close()
    return await service.upload_main_image(
        style_id,
        filename=image.filename,
        mime_type=image.content_type,
        data=data,
        user=user,
    )


@router.delete(
    "/styles/{style_id}/main-image",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("product", "write")],
)
async def remove_style_main_image(
    style_id: UUID,
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> Response:
    await service.remove_main_image(style_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    "/styles/{style_id}/enable",
    response_model=StyleResponse,
    dependencies=[require_permission("product", "write")],
)
async def enable_style(
    style_id: UUID,
    user: CurrentActiveUser,
    service: StyleServiceDep,
) -> StyleResponse:
    """重新启用被停用的款式（与 disable 对称，权限同为 product:write）。

    注意与下面的 ``restore`` 区分：restore 恢复的是**软删**（is_deleted），
    需要 product:delete 权限；本接口只改 is_active。
    """
    return await service.enable_style(style_id, user)


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


@router.get(
    "/skus/",
    response_model=CostTablePage,
    dependencies=[require_permission("product", "read")],
)
async def list_cost_table(
    user: CurrentActiveUser,
    service: SkuServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    keyword: str | None = Query(default=None, max_length=64),
    brand_id: UUID | None = Query(default=None),
    include_inactive: bool = Query(default=False),
) -> CostTablePage:
    """商品成本表（SKU 级，join 款式+品牌）— 对齐 final.xlsx 13 列。"""
    return await service.list_cost_table(
        keyword=keyword,
        brand_id=brand_id,
        include_inactive=include_inactive,
        page=page,
        page_size=page_size,
        user=user,
    )


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
    return await service.list_by_style(style_id, include_inactive=include_inactive, user=user)


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
    user: CurrentActiveUser,
    service: BrandServiceDep,
) -> BrandResponse:
    return await service.create_brand(payload)


@router.get(
    "/brands/",
    response_model=dict,
    dependencies=[require_permission("brand", "read")],
)
async def list_brands(
    user: CurrentActiveUser,
    service: BrandServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    is_active: bool | None = None,
) -> dict:
    items, total = await service.list_brands(is_active=is_active, page=page, page_size=page_size)
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
    user: CurrentActiveUser,
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
    user: CurrentActiveUser,
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
    user: CurrentActiveUser,
    service: BrandServiceDep,
) -> BrandResponse:
    """BR-U02-... 软停用品牌（不硬删）."""
    return await service.disable_brand(brand_id)


__all__ = ["router"]
