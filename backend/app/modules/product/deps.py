"""U02 product 模块 FastAPI 依赖注入。

复用 U01 ``modules/auth/deps.py`` 的 ``SessionDep`` / ``CurrentActiveUser``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.product.brand_service import BrandService
from app.modules.product.service import SkuService, StyleService
from app.modules.product.style_image_service import StyleImageBatchService


def get_style_service(session: SessionDep) -> StyleService:
    return StyleService(session)


def get_sku_service(session: SessionDep) -> SkuService:
    return SkuService(session)


def get_brand_service(session: SessionDep) -> BrandService:
    return BrandService(session)


StyleServiceDep = Annotated[StyleService, Depends(get_style_service)]
SkuServiceDep = Annotated[SkuService, Depends(get_sku_service)]
BrandServiceDep = Annotated[BrandService, Depends(get_brand_service)]


def get_style_image_batch_service(session: SessionDep) -> StyleImageBatchService:
    return StyleImageBatchService(session)


StyleImageBatchServiceDep = Annotated[
    StyleImageBatchService, Depends(get_style_image_batch_service)
]


__all__ = [
    "BrandServiceDep",
    "SkuServiceDep",
    "StyleImageBatchServiceDep",
    "StyleServiceDep",
    "get_brand_service",
    "get_sku_service",
    "get_style_image_batch_service",
    "get_style_service",
]
