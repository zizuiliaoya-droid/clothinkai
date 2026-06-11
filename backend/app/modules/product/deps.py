"""U02 product 模块 FastAPI 依赖注入。

复用 U01 ``modules/auth/deps.py`` 的 ``SessionDep`` / ``CurrentActiveUser``。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.modules.auth.deps import SessionDep
from app.modules.product.brand_service import BrandService
from app.modules.product.bundle_service import BundleService
from app.modules.product.service import SkuService, StyleService


def get_style_service(session: SessionDep) -> StyleService:
    return StyleService(session)


def get_sku_service(session: SessionDep) -> SkuService:
    return SkuService(session)


def get_brand_service(session: SessionDep) -> BrandService:
    return BrandService(session)


def get_bundle_service(session: SessionDep) -> BundleService:
    return BundleService(session)


StyleServiceDep = Annotated[StyleService, Depends(get_style_service)]
SkuServiceDep = Annotated[SkuService, Depends(get_sku_service)]
BrandServiceDep = Annotated[BrandService, Depends(get_brand_service)]
BundleServiceDep = Annotated[BundleService, Depends(get_bundle_service)]


__all__ = [
    "BrandServiceDep",
    "BundleServiceDep",
    "SkuServiceDep",
    "StyleServiceDep",
    "get_brand_service",
    "get_bundle_service",
    "get_sku_service",
    "get_style_service",
]
