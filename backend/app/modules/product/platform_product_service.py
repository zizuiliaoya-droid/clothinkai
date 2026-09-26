"""U10b PlatformProductService（平台商品映射 CRUD + 幂等 upsert + 反查）。"""

from __future__ import annotations

import builtins
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import DuplicateResourceError, ResourceNotFoundError, ValidationError
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
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

    async def _resolve_goods(self, style_id: UUID, goods_main_id: UUID | None) -> UUID | None:
        """商品归属：传了就校验商品确实包含该款式，没传就取主商品（非套装优先）。

        与推广记录同一套规则 —— 链接和推广都是「归到哪个商品」的问题。
        """
        if goods_main_id is not None:
            owns = (
                await self._session.execute(
                    select(func.count())
                    .select_from(GoodsStyleItem)
                    .where(
                        GoodsStyleItem.goods_main_id == goods_main_id,
                        GoodsStyleItem.style_id == style_id,
                        GoodsStyleItem.is_active.is_(True),
                    )
                )
            ).scalar_one()
            if not owns:
                raise ValidationError(
                    "goods_main_id 不包含该款式",
                    code="INVALID_GOODS_REFERENCE",
                    details={"goods_main_id": str(goods_main_id), "style_id": str(style_id)},
                )
            return goods_main_id
        stmt = (
            select(GoodsMain.id)
            .join(GoodsStyleItem, GoodsStyleItem.goods_main_id == GoodsMain.id)
            .where(
                GoodsStyleItem.style_id == style_id,
                GoodsStyleItem.is_active.is_(True),
                GoodsMain.is_deleted.is_(False),
            )
            .order_by(GoodsMain.is_suit, GoodsMain.goods_code)
            .limit(1)
        )
        resolved: UUID | None = (await self._session.execute(stmt)).scalar_one_or_none()
        return resolved

    # ------------------------------------------------------------------ #
    # 内部查找
    # ------------------------------------------------------------------ #
    async def _find(self, platform: str, platform_id: str) -> PlatformProduct | None:
        stmt = select(PlatformProduct).where(
            PlatformProduct.platform == platform,
            PlatformProduct.platform_id == platform_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _to_response(self, pp: PlatformProduct) -> PlatformProductResponse:
        """带上商品与款式信息 —— 运维视图要一眼看出这条链接连到哪。"""
        row = (
            await self._session.execute(
                text(
                    """
                    SELECT g.goods_code, g.goods_title, g.is_suit,
                           s.style_code, s.style_name
                    FROM platform_product pp
                    LEFT JOIN goods_main g ON g.id = pp.goods_main_id
                    LEFT JOIN style s ON s.id = pp.style_id
                    WHERE pp.id = :pp_id
                    """
                ),
                {"pp_id": pp.id},
            )
        ).one_or_none()
        resp = PlatformProductResponse.model_validate(pp)
        if row is not None:
            resp = resp.model_copy(
                update={
                    "goods_code": row[0],
                    "goods_title": row[1],
                    "goods_is_suit": bool(row[2]),
                    "style_code": row[3],
                    "style_name": row[4],
                }
            )
        return resp

    # ------------------------------------------------------------------ #
    # create（HTTP，严格新建）
    # ------------------------------------------------------------------ #
    async def create(
        self, payload: PlatformProductCreate, user_id: UUID
    ) -> PlatformProductResponse:
        await self._validate_refs(payload.style_id, payload.sku_id)
        goods_main_id = await self._resolve_goods(payload.style_id, payload.goods_main_id)
        pp = PlatformProduct(
            platform=payload.platform,
            platform_id=payload.platform_id,
            style_id=payload.style_id,
            sku_id=payload.sku_id,
            goods_main_id=goods_main_id,
            channel=payload.channel,
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
            action="platform_product.create",
            resource="platform_product",
            resource_id=pp.id,
            after={
                "platform": pp.platform,
                "platform_id": pp.platform_id,
                "style_id": str(pp.style_id),
                "goods_main_id": str(pp.goods_main_id) if pp.goods_main_id else None,
                "channel": pp.channel,
            },
            user_id=user_id,
        )
        await self._session.commit()
        return await self._to_response(pp)

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
            # 款式换了商品归属可能失效（旧商品未必含新款式），重新推定。
            # 人工指定过的归属只在仍然有效时保留。
            existing.goods_main_id = await self._resolve_goods_keep_if_valid(
                style_id, existing.goods_main_id
            )
            await self._session.flush()
            await self._audit.log(
                action="platform_product.update_via_import",
                resource="platform_product",
                resource_id=existing.id,
                after={"style_id": str(style_id)},
                user_id=user_id,
            )
            await self._session.commit()
            return existing
        pp = PlatformProduct(
            platform=platform,
            platform_id=platform_id,
            style_id=style_id,
            sku_id=sku_id,
            goods_main_id=await self._resolve_goods(style_id, None),
            title=title,
        )
        self._session.add(pp)
        await self._session.flush()
        await self._audit.log(
            action="platform_product.create_via_import",
            resource="platform_product",
            resource_id=pp.id,
            after={"platform": platform, "platform_id": platform_id, "style_id": str(style_id)},
            user_id=user_id,
        )
        await self._session.commit()
        return pp

    async def _resolve_goods_keep_if_valid(
        self, style_id: UUID, current: UUID | None
    ) -> UUID | None:
        """保留仍然有效的人工归属，否则重新推定主商品。"""
        if current is not None:
            still_valid = (
                await self._session.execute(
                    select(func.count())
                    .select_from(GoodsStyleItem)
                    .where(
                        GoodsStyleItem.goods_main_id == current,
                        GoodsStyleItem.style_id == style_id,
                        GoodsStyleItem.is_active.is_(True),
                    )
                )
            ).scalar_one()
            if still_valid:
                return current
        return await self._resolve_goods(style_id, None)

    # ------------------------------------------------------------------ #
    # find（反查，U13/U14）
    # ------------------------------------------------------------------ #
    async def find_by_platform_id(self, platform: str, platform_id: str) -> PlatformProduct | None:
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
        if payload.channel is not None:
            pp.channel = payload.channel
        if payload.is_active is not None:
            pp.is_active = payload.is_active
        # 归属显式改了就校验；只改了款式没改归属时重新推定（旧归属未必含新款式）
        if "goods_main_id" in payload.model_fields_set and payload.goods_main_id is not None:
            pp.goods_main_id = await self._resolve_goods(pp.style_id, payload.goods_main_id)
        elif payload.style_id is not None:
            pp.goods_main_id = await self._resolve_goods_keep_if_valid(
                pp.style_id, pp.goods_main_id
            )
        await self._session.flush()
        await self._audit.log(
            action="platform_product.update",
            resource="platform_product",
            resource_id=pp.id,
            after={
                "goods_main_id": str(pp.goods_main_id) if pp.goods_main_id else None,
                "channel": pp.channel,
                "is_active": pp.is_active,
            },
            user_id=user_id,
        )
        await self._session.commit()
        return await self._to_response(pp)

    async def delete(self, pp_id: UUID, user_id: UUID) -> None:
        pp = await self._session.get(PlatformProduct, pp_id)
        if pp is None:
            raise PlatformProductNotFoundError("平台商品映射不存在")
        await self._session.delete(pp)
        await self._audit.log(
            action="platform_product.delete",
            resource="platform_product",
            resource_id=pp.id,
            user_id=user_id,
        )
        await self._session.commit()

    async def list(
        self, *, tenant_id: UUID, style_id: UUID | None = None, page: int = 1, page_size: int = 20
    ) -> tuple[Sequence[PlatformProduct], int]:
        stmt = select(PlatformProduct).where(PlatformProduct.tenant_id == tenant_id)
        count_stmt = (
            select(func.count())
            .select_from(PlatformProduct)
            .where(PlatformProduct.tenant_id == tenant_id)
        )
        if style_id is not None:
            stmt = stmt.where(PlatformProduct.style_id == style_id)
            count_stmt = count_stmt.where(PlatformProduct.style_id == style_id)
        stmt = (
            stmt.order_by(PlatformProduct.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        total = (await self._session.execute(count_stmt)).scalar_one()
        return items, int(total)

    async def list_detailed(
        self,
        *,
        tenant_id: UUID,
        style_id: UUID | None = None,
        goods_main_id: UUID | None = None,
        platform: str | None = None,
        channel: str | None = None,
        keyword: str | None = None,
        unmapped_only: bool = False,
        page: int = 1,
        page_size: int = 20,
        # 本类有名为 ``list`` 的方法，会在类作用域内遮蔽内置 ``list``，
        # 故返回注解必须写 ``builtins.list``，否则会被解析成那个方法。
    ) -> tuple[builtins.list[PlatformProductResponse], int]:
        """运维视图列表：一次 JOIN 出商品与款式，避免逐行回表。

        ``keyword`` 同时搜平台ID、款式货号、款名与商品编码 —— 运维手里可能只有其中
        任意一个。``unmapped_only`` 用来捞「还没归到商品」的链接，那是数据缺口。
        """
        clauses = ["pp.tenant_id = :tenant_id"]
        params: dict[str, object] = {"tenant_id": tenant_id}
        if style_id is not None:
            clauses.append("pp.style_id = :style_id")
            params["style_id"] = style_id
        if goods_main_id is not None:
            clauses.append("pp.goods_main_id = :goods_main_id")
            params["goods_main_id"] = goods_main_id
        if platform:
            clauses.append("pp.platform = :platform")
            params["platform"] = platform
        if channel:
            clauses.append("pp.channel = :channel")
            params["channel"] = channel
        if unmapped_only:
            clauses.append("pp.goods_main_id IS NULL")
        if keyword:
            clauses.append(
                "(pp.platform_id ILIKE :kw OR s.style_code ILIKE :kw"
                " OR s.style_name ILIKE :kw OR g.goods_code ILIKE :kw)"
            )
            params["kw"] = f"%{keyword}%"
        where = " AND ".join(clauses)
        joins = """
            FROM platform_product pp
            LEFT JOIN goods_main g ON g.id = pp.goods_main_id
            LEFT JOIN style s ON s.id = pp.style_id
        """
        total = int(
            (
                await self._session.execute(text(f"SELECT COUNT(*) {joins} WHERE {where}"), params)
            ).scalar_one()
        )
        rows = (
            await self._session.execute(
                text(
                    f"""
                    SELECT pp.id, pp.platform, pp.platform_id, pp.style_id, pp.sku_id,
                           pp.goods_main_id, pp.channel, pp.title, pp.is_active,
                           pp.created_at, pp.updated_at,
                           g.goods_code, g.goods_title, COALESCE(g.is_suit, false) AS goods_is_suit,
                           s.style_code, s.style_name
                    {joins}
                    WHERE {where}
                    ORDER BY pp.platform, pp.platform_id
                    OFFSET :offset LIMIT :limit
                    """
                ),
                {**params, "offset": (page - 1) * page_size, "limit": page_size},
            )
        ).mappings()
        return [PlatformProductResponse.model_validate(dict(r)) for r in rows], total


__all__ = ["PlatformProductService"]
