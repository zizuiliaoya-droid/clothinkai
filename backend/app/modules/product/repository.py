"""U02 product 仓储层（StyleRepository / SkuRepository）。

按 nfr-design/logical-components.md §4.4-4.5：
- DB 操作（CRUD + 复杂查询）
- 不写业务规则
- 自动应用 RLS（依赖 Session 注入 tenant_id）
- ``StyleRepository.search_by_keyword``：使用 GIN trgm 索引
- ``SkuRepository.upsert_atomic``：使用 ON CONFLICT DO UPDATE 原子操作
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.product.models import Brand, Sku, Style


# ---------------------------------------------------------------------------
# StyleRepository
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StyleSearchResult:
    """模糊搜索的轻量返回结构（避免额外加载 ORM 关系）。"""

    id: UUID
    style_code: str
    style_name: str
    short_name: str | None


@dataclass(frozen=True)
class StyleListFilters:
    """列表查询筛选条件。"""

    keyword: str | None = None
    brand_id: UUID | None = None
    category: str | None = None
    season: str | None = None
    gender: str | None = None
    design_status: str | None = None
    is_active: bool | None = True
    include_inactive: bool = False


class StyleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get / count ----------------------- #

    async def get_by_id(
        self, style_id: UUID, *, include_deleted: bool = False
    ) -> Style | None:
        style = await self._session.get(Style, style_id)
        if style is None:
            return None
        if style.is_deleted and not include_deleted:
            return None
        return style

    async def get_by_code(
        self, style_code: str, *, include_deleted: bool = False
    ) -> Style | None:
        stmt = select(Style).where(Style.style_code == style_code)
        if not include_deleted:
            stmt = stmt.where(Style.is_deleted.is_(False))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def code_exists(self, style_code: str) -> bool:
        stmt = (
            select(sa.literal(1))
            .select_from(Style)
            .where(
                Style.style_code == style_code,
                Style.is_deleted.is_(False),
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    # ----------------------- list ----------------------- #

    async def list(
        self,
        *,
        filters: StyleListFilters,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[Sequence[Style], int]:
        stmt = select(Style).where(Style.is_deleted.is_(False))

        if not filters.include_inactive:
            stmt = stmt.where(Style.is_active.is_(True))

        if filters.keyword:
            pattern = f"%{filters.keyword}%"
            stmt = stmt.where(
                or_(
                    Style.style_code.ilike(pattern),
                    Style.style_name.ilike(pattern),
                    Style.short_name.ilike(pattern),
                )
            )
        if filters.brand_id is not None:
            stmt = stmt.where(Style.brand_id == filters.brand_id)
        if filters.category is not None:
            stmt = stmt.where(Style.category == filters.category)
        if filters.season is not None:
            stmt = stmt.where(Style.season == filters.season)
        if filters.gender is not None:
            stmt = stmt.where(Style.gender == filters.gender)
        if filters.design_status is not None:
            stmt = stmt.where(Style.design_status == filters.design_status)

        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self._session.execute(total_stmt)).scalar_one())

        stmt = (
            stmt.order_by(Style.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        return items, total

    # ----------------------- match (BR-U02-50/51) ----------------------- #

    async def search_by_keyword(
        self, keyword: str, *, limit: int = 20
    ) -> list[StyleSearchResult]:
        """模糊搜索（拼接表达式 ILIKE，命中 ``idx_style_search_trgm`` GIN 索引）。

        查询表达式必须与索引表达式严格一致，否则不命中：
        ``style_code || ' ' || style_name || ' ' || COALESCE(short_name, '')``
        """
        # 拼接表达式：与 idx_style_search_trgm 索引完全一致
        concat_expr = func.concat_ws(
            " ",
            Style.style_code,
            Style.style_name,
            func.coalesce(Style.short_name, ""),
        )
        sim = func.similarity(concat_expr, sa.bindparam("keyword"))
        pattern = f"%{keyword}%"
        exact = f"{keyword}%"

        order_priority = sa.case(
            (Style.short_name.ilike(exact), 1),
            (Style.style_name.ilike(exact), 2),
            else_=3,
        )

        stmt = (
            select(
                Style.id,
                Style.style_code,
                Style.style_name,
                Style.short_name,
            )
            .where(
                Style.is_deleted.is_(False),
                Style.is_active.is_(True),
                concat_expr.ilike(pattern),
            )
            .order_by(order_priority, sim.desc(), Style.created_at.desc())
            .limit(limit)
            .params(keyword=keyword)
        )

        rows = (await self._session.execute(stmt)).all()
        return [
            StyleSearchResult(
                id=row.id,
                style_code=row.style_code,
                style_name=row.style_name,
                short_name=row.short_name,
            )
            for row in rows
        ]

    # ----------------------- write ----------------------- #

    def add(self, style: Style) -> None:
        self._session.add(style)


# ---------------------------------------------------------------------------
# SkuRepository
# ---------------------------------------------------------------------------


class SkuRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self, sku_id: UUID, *, include_deleted: bool = False
    ) -> Sku | None:
        sku = await self._session.get(Sku, sku_id)
        if sku is None:
            return None
        if sku.is_deleted and not include_deleted:
            return None
        return sku

    async def get_by_code(
        self, sku_code: str, *, include_deleted: bool = False
    ) -> Sku | None:
        stmt = select(Sku).where(Sku.sku_code == sku_code)
        if not include_deleted:
            stmt = stmt.where(Sku.is_deleted.is_(False))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def code_exists(self, sku_code: str) -> bool:
        stmt = (
            select(sa.literal(1))
            .select_from(Sku)
            .where(Sku.sku_code == sku_code, Sku.is_deleted.is_(False))
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def list_by_style(
        self, style_id: UUID, *, include_inactive: bool = False
    ) -> Sequence[Sku]:
        stmt = (
            select(Sku)
            .where(
                Sku.style_id == style_id,
                Sku.is_deleted.is_(False),
            )
            .order_by(Sku.size.asc(), Sku.color.asc(), Sku.sku_code.asc())
            .limit(1000)
        )
        if not include_inactive:
            stmt = stmt.where(Sku.is_active.is_(True))
        return (await self._session.execute(stmt)).scalars().all()

    async def count_by_style(
        self,
        style_id: UUID,
        *,
        is_active: bool | None = None,
        is_deleted: bool = False,
    ) -> int:
        stmt = select(func.count(Sku.id)).where(
            Sku.style_id == style_id,
            Sku.is_deleted.is_(is_deleted),
        )
        if is_active is not None:
            stmt = stmt.where(Sku.is_active.is_(is_active))
        return int((await self._session.execute(stmt)).scalar_one())

    # ----------------------- upsert (BR-U02-13 + Pattern P-U02-03) ----------------------- #

    async def upsert_atomic(
        self,
        *,
        tenant_id: UUID,
        values: dict[str, Any],
    ) -> tuple[Sku, bool]:
        """``ON CONFLICT (tenant_id, sku_code) WHERE is_deleted=false DO UPDATE``。

        Returns:
            ``(sku, is_inserted)`` —— is_inserted=True 表示 INSERT 路径；
            False 表示 UPDATE 路径。

        通过 ``RETURNING (xmax = 0) AS is_inserted`` 判断（PostgreSQL 内部 MVCC）。

        约束：
        - ``values`` 必须包含 ``sku_code, style_id, color, size, sourcing_type``
        - 不更新 ``id`` / ``created_at`` / ``style_id`` / ``sku_code``
        """
        # 排除 INSERT-only 字段（防止重写）
        update_fields = {
            k: v
            for k, v in values.items()
            if k
            not in {
                "id",
                "tenant_id",
                "created_at",
                "style_id",
                "sku_code",
                "is_deleted",
            }
        }
        update_fields["updated_at"] = sa.func.now()

        full_values = {"tenant_id": tenant_id, **values}

        stmt = pg_insert(Sku).values(**full_values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Sku.tenant_id, Sku.sku_code],
            # 谓词须与 partial UNIQUE 索引匹配（migration 用 ``is_deleted = false``）；
            # ``.is_(False)`` 生成 ``IS false`` 会导致 ON CONFLICT 无法匹配索引。
            index_where=sa.text("is_deleted = false"),
            set_=update_fields,
        ).returning(
            Sku,
            sa.text("(xmax = 0) AS is_inserted"),
        )

        result = await self._session.execute(stmt)
        row = result.one()
        sku: Sku = row[0]
        # 混合 ORM 实体 + 原生列时按位置取（``row.is_inserted`` 在 ORM 行不可用）
        is_inserted: bool = bool(row[1])
        return sku, is_inserted

    # ----------------------- write ----------------------- #

    def add(self, sku: Sku) -> None:
        self._session.add(sku)


__all__ = [
    "BrandRepository",
    "SkuRepository",
    "StyleListFilters",
    "StyleRepository",
    "StyleSearchResult",
]


from app.modules.product.brand_repository import BrandRepository  # re-export
