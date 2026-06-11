"""Brand 字典服务（简化 CRUD）。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import ResourceNotFoundError
from app.modules.product.brand_repository import BrandRepository
from app.modules.product.brand_schemas import (
    BrandCreate,
    BrandResponse,
    BrandUpdate,
)
from app.modules.product.exceptions import (
    BrandCodeConflictError,
    BrandNotFoundError,
)
from app.modules.product.models import Brand


class BrandService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = BrandRepository(session)
        self._audit = AuditService(session)

    async def create_brand(self, payload: BrandCreate) -> BrandResponse:
        existing = await self._repo.get_by_code(payload.brand_code)
        if existing is not None:
            raise BrandCodeConflictError(
                f"品牌编码 {payload.brand_code} 已被使用",
                details={"brand_code": payload.brand_code},
            )
        brand = Brand(
            brand_code=payload.brand_code,
            brand_name=payload.brand_name,
        )
        self._repo.add(brand)
        await self._session.flush()
        await self._session.commit()
        return BrandResponse.model_validate(brand)

    async def update_brand(
        self, brand_id: UUID, payload: BrandUpdate
    ) -> BrandResponse:
        brand = await self._repo.get_by_id(brand_id)
        if brand is None:
            raise BrandNotFoundError(f"品牌 {brand_id} 不存在")

        if payload.brand_name is not None:
            brand.brand_name = payload.brand_name
        if payload.is_active is not None:
            brand.is_active = payload.is_active
        await self._session.flush()
        await self._session.commit()
        return BrandResponse.model_validate(brand)

    async def disable_brand(self, brand_id: UUID) -> BrandResponse:
        brand = await self._repo.get_by_id(brand_id)
        if brand is None:
            raise BrandNotFoundError(f"品牌 {brand_id} 不存在")
        brand.is_active = False
        await self._session.flush()
        await self._session.commit()
        return BrandResponse.model_validate(brand)

    async def get_brand(self, brand_id: UUID) -> BrandResponse:
        brand = await self._repo.get_by_id(brand_id)
        if brand is None:
            raise BrandNotFoundError(f"品牌 {brand_id} 不存在")
        return BrandResponse.model_validate(brand)

    async def list_brands(
        self,
        *,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[BrandResponse], int]:
        items, total = await self._repo.list(
            is_active=is_active, page=page, page_size=page_size
        )
        return [BrandResponse.model_validate(b) for b in items], total


__all__ = ["BrandService"]
