"""商品（goods_main）服务层：CRUD + 成员维护 + 删除保护。

商品是报表归属的主体与平台链接的归属对象，所以写操作比普通字典严格：

- ``goods_code`` 建档后不可改 —— 报表、链接、推广都按它引用商品。
- ``is_suit`` 由成员数推导（≥2 即套装），不接受前端传值，否则两个字段会互相矛盾。
- 仍挂着平台链接的商品不许删 —— 先把链接改到别的商品上，否则销售数据失去归属。
- 删除是软删并留痕（谁删、何时删），PRD 要求历史数据不被物理清掉。
"""

from __future__ import annotations

import builtins
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import (
    AppException,
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.goods_repository import GoodsListFilters, GoodsRepository
from app.modules.product.goods_schemas import (
    GoodsMainCreate,
    GoodsMainResponse,
    GoodsMainUpdate,
    GoodsStyleItemIn,
    GoodsStyleItemResponse,
)
from app.modules.product.models import Brand, Style


class GoodsCodeConflictError(DuplicateResourceError):
    code = "GOODS_CODE_CONFLICT"
    status_code = 409


class GoodsNotFoundError(ResourceNotFoundError):
    code = "GOODS_NOT_FOUND"


class GoodsHasLinksError(AppException):
    """商品仍挂着平台链接，不能删 —— 删了销售数据就没有归属对象了。"""

    code = "GOODS_HAS_LINKS"
    status_code = 409


class GoodsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = GoodsRepository(session)
        self._audit = AuditService(session)

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #

    async def _validate_items(
        self, items: builtins.list[GoodsStyleItemIn]
    ) -> builtins.list[GoodsStyleItemIn]:
        """成员款式必须存在、未删，且同一商品内不重复。"""
        if not items:
            raise ValidationError(
                "商品至少要含 1 个款式",
                code="GOODS_NO_ITEM",
            )
        seen: set[UUID] = set()
        for item in items:
            if item.style_id in seen:
                raise ValidationError(
                    "同一商品内款式不可重复",
                    code="GOODS_DUPLICATE_STYLE",
                    details={"style_id": str(item.style_id)},
                )
            seen.add(item.style_id)
            style = await self._session.get(Style, item.style_id)
            if style is None or style.is_deleted:
                raise ValidationError(
                    "style_id 不存在或已删除",
                    code="INVALID_STYLE_REFERENCE",
                    details={"style_id": str(item.style_id)},
                )
        return items

    # ------------------------------------------------------------------ #
    # 成员写入
    # ------------------------------------------------------------------ #

    async def _write_items(self, goods: GoodsMain, items: builtins.list[GoodsStyleItemIn]) -> None:
        """整体替换成员行，并把 ``is_suit`` 同步为「成员数 ≥ 2」。

        没填成本的成员回落到款式 SKU 的成本价 —— 建档时少填一个字段，
        套装总成本仍然算得出来。
        """
        await self._repo.delete_items(goods.id)
        for order, item in enumerate(items):
            cost = item.single_goods_cost
            if cost is None:
                cost = await self._repo.default_cost_for_style(item.style_id)
            self._repo.add_item(
                GoodsStyleItem(
                    tenant_id=goods.tenant_id,
                    goods_main_id=goods.id,
                    style_id=item.style_id,
                    single_goods_cost=cost,
                    sort_order=item.sort_order or order,
                )
            )
        goods.is_suit = len(items) >= 2

    # ------------------------------------------------------------------ #
    # 出参
    # ------------------------------------------------------------------ #

    async def _to_response(self, goods: GoodsMain) -> GoodsMainResponse:
        item_rows = (await self._repo.items_by_goods_ids([goods.id])).get(goods.id, [])
        items = [
            GoodsStyleItemResponse(
                id=r["id"],
                style_id=r["style_id"],
                style_code=r["style_code"],
                style_name=r["style_name"],
                single_goods_cost=r["single_goods_cost"],
                sort_order=r["sort_order"],
                is_active=r["is_active"],
            )
            for r in item_rows
        ]
        active = [i for i in items if i.is_active]
        costs = [i.single_goods_cost for i in active if i.single_goods_cost is not None]
        brand_name: str | None = None
        if goods.brand_id is not None:
            brand = await self._session.get(Brand, goods.brand_id)
            brand_name = brand.brand_name if brand is not None else None
        refs = await self._repo.reference_counts(goods.id)
        return GoodsMainResponse(
            id=goods.id,
            goods_code=goods.goods_code,
            goods_title=goods.goods_title,
            short_name=goods.short_name,
            category=goods.category,
            season=goods.season,
            brand_id=goods.brand_id,
            brand_name=brand_name,
            main_image_key=goods.main_image_key,
            remark=goods.remark,
            is_suit=goods.is_suit,
            is_active=goods.is_active,
            created_at=goods.created_at,
            updated_at=goods.updated_at,
            items=items,
            total_cost=sum(costs, Decimal("0")) if costs else None,
            cost_missing_count=len(active) - len(costs),
            link_count=refs["platform_links"],
        )

    # ------------------------------------------------------------------ #
    # create
    # ------------------------------------------------------------------ #

    async def create(
        self, payload: GoodsMainCreate, *, tenant_id: UUID, user_id: UUID
    ) -> GoodsMainResponse:
        items = await self._validate_items(payload.items)
        if await self._repo.code_exists(payload.goods_code):
            raise GoodsCodeConflictError(
                f"商品编码已存在 ({payload.goods_code})",
                details={"goods_code": payload.goods_code},
            )
        goods = GoodsMain(
            tenant_id=tenant_id,
            goods_code=payload.goods_code,
            goods_title=payload.goods_title,
            short_name=payload.short_name,
            category=payload.category,
            season=payload.season,
            main_image_key=payload.main_image_key,
            remark=payload.remark,
        )
        self._repo.add(goods)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            if "uq_goods_main_code" in str(getattr(exc, "orig", exc)):
                raise GoodsCodeConflictError(
                    f"商品编码已存在 ({payload.goods_code})",
                    details={"goods_code": payload.goods_code},
                ) from exc
            raise
        await self._write_items(goods, items)
        await self._session.flush()
        await self._audit.log(
            action="goods.create",
            resource="goods_main",
            resource_id=goods.id,
            after={
                "goods_code": goods.goods_code,
                "goods_title": goods.goods_title,
                "short_name": goods.short_name,
                "is_suit": goods.is_suit,
                "style_count": len(items),
            },
            user_id=user_id,
        )
        await self._session.commit()
        return await self._to_response(goods)

    # ------------------------------------------------------------------ #
    # update
    # ------------------------------------------------------------------ #

    async def update(
        self, goods_id: UUID, payload: GoodsMainUpdate, *, user_id: UUID
    ) -> GoodsMainResponse:
        goods = await self._repo.get_by_id(goods_id)
        if goods is None:
            raise GoodsNotFoundError("商品不存在")
        before: dict[str, Any] = {
            "goods_title": goods.goods_title,
            "short_name": goods.short_name,
            "season": goods.season,
            "is_suit": goods.is_suit,
            "is_active": goods.is_active,
        }
        # 品牌只读（8a-4，A12）：不再由商品接口改，只由商品资料导入与冲突裁决写入
        if payload.goods_title is not None:
            goods.goods_title = payload.goods_title
        # 简称可以清空：按「有没有传」判断，而不是「是不是 None」（见 GoodsMainUpdate）
        if "short_name" in payload.model_fields_set:
            goods.short_name = payload.short_name
        if payload.category is not None:
            goods.category = payload.category
        if payload.season is not None:
            goods.season = payload.season
        if payload.main_image_key is not None:
            goods.main_image_key = payload.main_image_key
        if payload.remark is not None:
            goods.remark = payload.remark
        if payload.is_active is not None:
            goods.is_active = payload.is_active
        if payload.items is not None:
            items = await self._validate_items(payload.items)
            await self._write_items(goods, items)
        await self._session.flush()
        await self._audit.log(
            action="goods.update",
            resource="goods_main",
            resource_id=goods.id,
            before=before,
            after={
                "goods_title": goods.goods_title,
                "short_name": goods.short_name,
                # 运营也能改季节（8a-7），靠审计留痕（J48）
                "season": goods.season,
                "is_suit": goods.is_suit,
                "is_active": goods.is_active,
            },
            user_id=user_id,
        )
        await self._session.commit()
        return await self._to_response(goods)

    # ------------------------------------------------------------------ #
    # delete（软删 + 留痕）
    # ------------------------------------------------------------------ #

    async def soft_delete(self, goods_id: UUID, *, user_id: UUID) -> None:
        goods = await self._repo.get_by_id(goods_id)
        if goods is None:
            raise GoodsNotFoundError("商品不存在")
        refs = await self._repo.reference_counts(goods_id)
        if refs["platform_links"]:
            raise GoodsHasLinksError(
                f"该商品还挂着 {refs['platform_links']} 条平台链接，"
                "请先在平台链接页把它们改到别的商品上",
                details=refs,
            )
        goods.is_deleted = True
        goods.is_active = False
        goods.deleted_at = datetime.now(UTC)
        goods.deleted_by = user_id
        await self._session.flush()
        await self._audit.log(
            action="goods.delete",
            resource="goods_main",
            resource_id=goods.id,
            before={"goods_code": goods.goods_code, "goods_title": goods.goods_title},
            after={"is_deleted": True, "promotions_kept": refs["promotions"]},
            user_id=user_id,
        )
        await self._session.commit()

    # ------------------------------------------------------------------ #
    # read
    # ------------------------------------------------------------------ #

    async def get(self, goods_id: UUID) -> GoodsMainResponse:
        goods = await self._repo.get_by_id(goods_id)
        if goods is None:
            raise GoodsNotFoundError("商品不存在")
        return await self._to_response(goods)

    async def list_goods(
        self,
        *,
        tenant_id: UUID,
        filters: GoodsListFilters,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[builtins.list[GoodsMainResponse], int]:
        rows, total = await self._repo.list(
            tenant_id=tenant_id, filters=filters, page=page, page_size=page_size
        )
        if not rows:
            return [], total
        items_map = await self._repo.items_by_goods_ids([r["id"] for r in rows])
        out: builtins.list[GoodsMainResponse] = []
        for r in rows:
            item_rows = items_map.get(r["id"], [])
            out.append(
                GoodsMainResponse(
                    id=r["id"],
                    goods_code=r["goods_code"],
                    goods_title=r["goods_title"],
                    short_name=r["short_name"],
                    category=r["category"],
                    season=r["season"],
                    brand_id=r["brand_id"],
                    brand_name=r["brand_name"],
                    main_image_key=r["main_image_key"],
                    remark=r["remark"],
                    is_suit=r["is_suit"],
                    is_active=r["is_active"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    items=[
                        GoodsStyleItemResponse(
                            id=i["id"],
                            style_id=i["style_id"],
                            style_code=i["style_code"],
                            style_name=i["style_name"],
                            single_goods_cost=i["single_goods_cost"],
                            sort_order=i["sort_order"],
                            is_active=i["is_active"],
                        )
                        for i in item_rows
                    ],
                    total_cost=r["total_cost"],
                    cost_missing_count=int(r["cost_missing_count"]),
                    link_count=int(r["link_count"]),
                )
            )
        return out, total


__all__ = [
    "GoodsCodeConflictError",
    "GoodsHasLinksError",
    "GoodsNotFoundError",
    "GoodsService",
]
