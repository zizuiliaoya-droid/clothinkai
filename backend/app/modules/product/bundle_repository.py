"""U17 套装仓储（bundle + items）。RLS 自动隔离 + 显式 tenant 校验。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.product.bundle_models import BundleItem, BundleProduct
from app.modules.product.models import Sku


class BundleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, bundle: BundleProduct) -> None:
        self._s.add(bundle)

    def add_item(self, item: BundleItem) -> None:
        self._s.add(item)

    async def get(self, bundle_id: UUID) -> BundleProduct | None:
        return await self._s.get(BundleProduct, bundle_id)

    async def list_items(self, bundle_id: UUID) -> Sequence[BundleItem]:
        stmt = select(BundleItem).where(BundleItem.bundle_id == bundle_id)
        return (await self._s.execute(stmt)).scalars().all()

    async def list_bundles(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[BundleProduct]:
        stmt = (
            select(BundleProduct)
            .order_by(BundleProduct.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._s.execute(stmt)).scalars().all()

    async def sku_exists(self, sku_id: UUID, tenant_id: UUID) -> bool:
        stmt = (
            select(func.count())
            .select_from(Sku)
            .where(Sku.id == sku_id, Sku.tenant_id == tenant_id)
        )
        return int((await self._s.execute(stmt)).scalar_one()) > 0


__all__ = ["BundleRepository"]
