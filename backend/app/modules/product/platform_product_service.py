"""U10b PlatformProductService（平台商品映射 CRUD + 幂等 upsert + 反查）。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import DuplicateResourceError, ResourceNotFoundError, ValidationError
from app.modules.product.models import Sku, Style
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.product.platform_product_schemas import (
    PlatformProductCreate,
    PlatformProductResponse,
    PlatformProductUpdate,
)


class PlatformProductConflictError(DuplicateResourceError):
    code = "PLATFORM_PRODUCT_CONFLICT"
    status_code = 409


class PlatformProductNotFoundError(ResourceNotFoundError):
    code = "PLATFORM_PRODUCT_NOT_FOUND"


class PlatformProductService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    # ------------------------------------------------------------------ #
    # 引用校验
    # ------------------------------------------------------------------ #
    async def _validate_refs(self, style_id: UUID, sku_id: UUID | None) -> None:
        style = await self._session.get(Style, style_id)
        if style is None or style.is_deleted:
            raise ValidationError(
                "style_id 不存在或已删除",
                code="INVALID_STYLE_REFERENCE",
                details={"style_id": str(style_id)},
            )
        if sku_id is not None:
            sku = await self._session.get(Sku, sku_id)
            if sku is None or sku.is_deleted or sku.style_id != style_id:
                raise ValidationError(
                    "sku_id 不存在或不属于该 style",
                    code="INVALID_SKU_REFERENCE",
                    details={"sku_id": str(sku_id), "style_id": str(style_id)},
                )

    # ------------------------------------------------------------------ #
    # 内部查找
    # ------------------------------------------------------------------ #
    async def _find(self, platform: str, platform_id: str) -> PlatformProduct | None:
        stmt = select(PlatformProduct).where(
            PlatformProduct.platform == platform,
            PlatformProduct.platform_id == platform_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    # ------------------------------------------------------------------ #
    # create（HTTP，严格新建）
    # ------------------------------------------------------------------ #
    async def create(
        self, payload: PlatformProductCreate, user_id: UUID
    ) -> PlatformProductResponse:
        await self._validate_refs(payload.style_id, payload.sku_id)
        pp = PlatformProduct(
            platform=payload.platform,
            platform_id=payload.platform_id,
            style_id=payload.style_id,
            sku_id=payload.sku_id,
            title=payload.title,
        )
        self._session.add(pp)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            if "uq_platform_product_" in str(getattr(exc, "orig", exc)):
                existing = await self._find(payload.platform, payload.platform_id)
                raise PlatformProductConflictError(
                    f"平台商品映射已存在 ({payload.platform}/{payload.platform_id})",
                    details={"existing_id": str(existing.id) if existing else None},
                ) from exc
            raise
        await self._audit.log(
            action="platform_product.create", resource="platform_product",
            resource_id=pp.id,
            after={"platform": pp.platform, "platform_id": pp.platform_id,
                   "style_id": str(pp.style_id)},
            user_id=user_id,
        )
        await self._session.commit()
        return PlatformProductResponse.model_validate(pp)

    # ------------------------------------------------------------------ #
    # create_or_update（内部导入路径，幂等）
    # ------------------------------------------------------------------ #
    async def create_or_update(
        self,
        *,
        platform: str,
        platform_id: str,
        style_id: UUID,
        sku_id: UUID | None = None,
        title: str | None = None,
        user_id: UUID,
    ) -> PlatformProduct:
        await self._validate_refs(style_id, sku_id)
        existing = await self._find(platform, platform_id)
        if existing is not None:
            existing.style_id = style_id
            existing.sku_id = sku_id
            existing.title = title
            existing.is_active = True
            await self._session.flush()
            await self._audit.log(
                action="platform_product.update_via_import",
                resource="platform_product", resource_id=existing.id,
                after={"style_id": str(style_id)}, user_id=user_id,
            )
            await self._session.commit()
            return existing
        pp = PlatformProduct(
            platform=platform, platform_id=platform_id,
            style_id=style_id, sku_id=sku_id, title=title,
        )
        self._session.add(pp)
        await self._session.flush()
        await self._audit.log(
            action="platform_product.create_via_import",
            resource="platform_product", resource_id=pp.id,
            after={"platform": platform, "platform_id": platform_id,
                   "style_id": str(style_id)},
            user_id=user_id,
        )
        await self._session.commit()
        return pp

    # ------------------------------------------------------------------ #
    # find（反查，U13/U14）
    # ------------------------------------------------------------------ #
    async def find_by_platform_id(
        self, platform: str, platform_id: str
    ) -> PlatformProduct | None:
        return await self._find(platform, platform_id)

    # ------------------------------------------------------------------ #
    # update / delete / list
    # ------------------------------------------------------------------ #
    async def update(
        self, pp_id: UUID, payload: PlatformProductUpdate, user_id: UUID
    ) -> PlatformProductResponse:
        pp = await self._session.get(PlatformProduct, pp_id)
        if pp is None:
            raise PlatformProductNotFoundError("平台商品映射不存在")
        if payload.style_id is not None:
            await self._validate_refs(payload.style_id, payload.sku_id)
            pp.style_id = payload.style_id
        if payload.sku_id is not None:
            pp.sku_id = payload.sku_id
        if payload.title is not None:
            pp.title = payload.title
        if payload.is_active is not None:
            pp.is_active = payload.is_active
        await self._session.flush()
        await self._audit.log(
            action="platform_product.update", resource="platform_product",
            resource_id=pp.id, user_id=user_id,
        )
        await self._session.commit()
        return PlatformProductResponse.model_validate(pp)

    async def delete(self, pp_id: UUID, user_id: UUID) -> None:
        pp = await self._session.get(PlatformProduct, pp_id)
        if pp is None:
            raise PlatformProductNotFoundError("平台商品映射不存在")
        await self._session.delete(pp)
        await self._audit.log(
            action="platform_product.delete", resource="platform_product",
            resource_id=pp.id, user_id=user_id,
        )
        await self._session.commit()

    async def list(
        self, *, tenant_id: UUID, style_id: UUID | None = None,
        page: int = 1, page_size: int = 20
    ) -> tuple[Sequence[PlatformProduct], int]:
        stmt = select(PlatformProduct).where(PlatformProduct.tenant_id == tenant_id)
        count_stmt = select(func.count()).select_from(PlatformProduct).where(
            PlatformProduct.tenant_id == tenant_id
        )
        if style_id is not None:
            stmt = stmt.where(PlatformProduct.style_id == style_id)
            count_stmt = count_stmt.where(PlatformProduct.style_id == style_id)
        stmt = stmt.order_by(PlatformProduct.created_at.desc()).offset(
            (page - 1) * page_size
        ).limit(page_size)
        items = (await self._session.execute(stmt)).scalars().all()
        total = (await self._session.execute(count_stmt)).scalar_one()
        return items, int(total)


__all__ = ["PlatformProductService"]
