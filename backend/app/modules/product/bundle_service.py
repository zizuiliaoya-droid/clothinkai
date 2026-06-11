"""U17 BundleService（套装创建 + 销量拆分）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import (
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.modules.product.bundle_models import BundleItem, BundleProduct
from app.modules.product.bundle_repository import BundleRepository
from app.modules.product.bundle_schemas import BundleCreate


class BundleService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = BundleRepository(session)

    async def create(self, payload: BundleCreate, user: Any) -> BundleProduct:
        if not payload.items:
            raise ValidationError("套装至少含 1 个组合项")
        seen: set[UUID] = set()
        for it in payload.items:
            if it.quantity < 1:
                raise ValidationError("quantity 须 >= 1")
            if it.sku_id in seen:
                raise ValidationError("同一套装 sku 不可重复")
            seen.add(it.sku_id)
            if not await self._repo.sku_exists(it.sku_id, user.tenant_id):
                raise ValidationError("sku 不存在或跨租户")
        bundle = BundleProduct(
            tenant_id=user.tenant_id,
            bundle_code=payload.bundle_code,
            bundle_name=payload.bundle_name,
            remark=payload.remark,
            is_active=True,
        )
        self._repo.add(bundle)
        try:
            await self._s.flush()
        except IntegrityError as exc:
            raise DuplicateResourceError("bundle_code 已存在") from exc
        for it in payload.items:
            self._repo.add_item(BundleItem(
                tenant_id=user.tenant_id, bundle_id=bundle.id,
                sku_id=it.sku_id, quantity=it.quantity,
            ))
        await self._s.flush()
        await AuditService(self._s).log(
            "product.bundle.create",
            resource="bundle_product",
            resource_id=bundle.id,
            user_id=user.id,
        )
        await self._s.commit()
        return bundle

    async def get_with_items(
        self, bundle_id: UUID
    ) -> tuple[BundleProduct, list[BundleItem]]:
        bundle = await self._repo.get(bundle_id)
        if bundle is None:
            raise ResourceNotFoundError("套装不存在")
        items = list(await self._repo.list_items(bundle_id))
        return bundle, items

    async def list_bundles(self, *, limit: int = 50, offset: int = 0):
        return await self._repo.list_bundles(limit=limit, offset=offset)

    async def split_quantities(
        self, bundle_id: UUID, sold_qty: int
    ) -> list[tuple[UUID, int]]:
        """EP02-S08：销量按 item 数量拆分到各 sku。"""
        items = await self._repo.list_items(bundle_id)
        return [(it.sku_id, it.quantity * sold_qty) for it in items]


__all__ = ["BundleService"]
