"""Brand 字典仓储。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.product.models import Brand


class BrandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, brand_id: UUID) -> Brand | None:
        return await self._session.get(Brand, brand_id)

    async def get_by_code(self, brand_code: str) -> Brand | None:
        stmt = select(Brand).where(Brand.brand_code == brand_code)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list(
        self,
        *,
        is_active: bool | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[Sequence[Brand], int]:
        stmt = select(Brand)
        if is_active is not None:
            stmt = stmt.where(Brand.is_active.is_(is_active))

        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self._session.execute(total_stmt)).scalar_one())

        stmt = (
            stmt.order_by(Brand.brand_code.asc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        return items, total

    def add(self, brand: Brand) -> None:
        self._session.add(brand)


__all__ = ["BrandRepository"]
