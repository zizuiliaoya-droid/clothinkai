"""商品（goods_main）服务层：CRUD + 成员维护 + 删除保护。

商品是报表归属的主体与平台链接的归属对象，所以写操作比普通字典严格：

- ``goods_code`` 建档后不可改 —— 报表、链接、推广都按它引用商品。
- ``is_suit`` 由成员数推导（≥2 即套装），不接受前端传值，否则两个字段会互相矛盾。
- 仍挂着平台链接的商品不许删 —— 先把链接改到别的商品上，否则销售数据失去归属。
- 删除是软删并留痕（谁删、何时删），PRD 要求历史数据不被物理清掉。
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
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
from app.modules.product.goods_codes import GoodsCodeExhaustedError, generate_goods_code
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.goods_repository import GoodsListFilters, GoodsRepository
from app.modules.product.goods_schemas import (
    GoodsImage,
    GoodsMainCreate,
    GoodsMainResponse,
    GoodsMainUpdate,
    GoodsStyleItemIn,
    GoodsStyleItemResponse,
)
from app.modules.product.images import resolve_style_image
from app.modules.product.models import Brand, Style

CODE_GENERATE_ATTEMPTS = 3
"""系统生成编码时，插入撞唯一索引（并发新建）后重新生成的次数上限。"""


class GoodsCodeConflictError(DuplicateResourceError):
    code = "GOODS_CODE_CONFLICT"
    status_code = 409


class GoodsNotFoundError(ResourceNotFoundError):
    code = "GOODS_NOT_FOUND"


class GoodsHasLinksError(AppException):
    """商品仍挂着平台链接，不能删 —— 删了销售数据就没有归属对象了。"""

    code = "GOODS_HAS_LINKS"
    status_code = 409


def goods_images(item_rows: Sequence[Mapping[str, Any]]) -> builtins.list[GoodsImage]:
    """商品图由启用成员款式派生（8a-2，§7.2）。

    ``item_rows`` 已按成员顺序（``sort_order, style_code``）排好（``items_by_goods_ids``）；
    ``resolve_style_image`` 有结果的成员才放进来——缺图的成员不占位。
    ``goods_main.main_image_key`` 已废弃，不读。
    """
    out: builtins.list[GoodsImage] = []
    for row in item_rows:
        if not row["is_active"] or row.get("style_code") is None:
            continue
        image = resolve_style_image(row.get("main_image_key"), row.get("external_image_url"))
        if image is not None:
            out.append(
                GoodsImage(
                    style_id=row["style_id"],
                    style_code=row["style_code"],
                    url=image.url,
                    source=image.source,
                )
            )
    return out


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
            season=goods.season,
            brand_id=goods.brand_id,
            brand_name=brand_name,
            remark=goods.remark,
            is_suit=goods.is_suit,
            is_active=goods.is_active,
            created_at=goods.created_at,
            updated_at=goods.updated_at,
            images=goods_images(item_rows),
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
        notices: builtins.list[str] = []
        if payload.goods_code is not None:
            goods = await self._insert_with_code(payload, payload.goods_code, tenant_id=tenant_id)
        else:
            goods, notice = await self._insert_generated(payload, items, tenant_id=tenant_id)
            if notice:
                notices.append(notice)
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
        resp = await self._to_response(goods)
        return resp.model_copy(update={"notices": notices}) if notices else resp

    def _new_goods(self, payload: GoodsMainCreate, goods_code: str, tenant_id: UUID) -> GoodsMain:
        return GoodsMain(
            tenant_id=tenant_id,
            goods_code=goods_code,
            goods_title=payload.goods_title,
            short_name=payload.short_name,
            season=payload.season,
            remark=payload.remark,
        )

    async def _insert_with_code(
        self, payload: GoodsMainCreate, goods_code: str, *, tenant_id: UUID
    ) -> GoodsMain:
        """调用方指定了编码（脚本 / 测试兼容的旧路径）：被占用（含已软删）→ 409。"""
        if await self._repo.code_exists(goods_code):
            raise GoodsCodeConflictError(
                f"商品编码已存在 ({goods_code})",
                details={"goods_code": goods_code},
            )
        goods = self._new_goods(payload, goods_code, tenant_id)
        self._repo.add(goods)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            if "uq_goods_main_code" in str(getattr(exc, "orig", exc)):
                raise GoodsCodeConflictError(
                    f"商品编码已存在 ({goods_code})",
                    details={"goods_code": goods_code},
                ) from exc
            raise
        return goods

    async def _insert_generated(
        self,
        payload: GoodsMainCreate,
        items: builtins.list[GoodsStyleItemIn],
        *,
        tenant_id: UUID,
    ) -> tuple[GoodsMain, str | None]:
        """编码由系统生成（补充 3，§11.2）。

        预检（``code_exists``）与插入之间可能被并发新建抢走同一个编码：插入放在保存点里，
        撞 ``uq_goods_main_code`` 就回滚保存点、重新生成，最多 ``CODE_GENERATE_ATTEMPTS`` 次。
        409 的提示不带编码（界面不显示编码，补充 2）。
        """
        member_codes: builtins.list[str] = []
        for item in items:
            # _validate_items 已校验存在且未删，这里从会话的身份映射里取
            style = await self._session.get(Style, item.style_id)
            if style is not None:
                member_codes.append(style.style_code)
        for _ in range(CODE_GENERATE_ATTEMPTS):
            try:
                code, notice = await generate_goods_code(self._repo, member_codes)
            except GoodsCodeExhaustedError as exc:
                raise GoodsCodeConflictError("没能为新商品生成可用的内部编码，请重试") from exc
            goods = self._new_goods(payload, code, tenant_id)
            try:
                async with self._session.begin_nested():
                    self._repo.add(goods)
                    await self._session.flush()
            except IntegrityError as exc:
                if "uq_goods_main_code" in str(getattr(exc, "orig", exc)):
                    continue  # 并发：刚被别人用了，重新生成
                raise
            return goods, notice
        raise GoodsCodeConflictError("没能为新商品生成可用的内部编码，请重试")

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
        if payload.season is not None:
            goods.season = payload.season
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
                    season=r["season"],
                    brand_id=r["brand_id"],
                    brand_name=r["brand_name"],
                    remark=r["remark"],
                    is_suit=r["is_suit"],
                    is_active=r["is_active"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    images=goods_images(item_rows),
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
