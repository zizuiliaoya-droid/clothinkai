"""U03 blogger 仓储层。

- ``BloggerRepository.list``：含 ``include_wechat_in_keyword`` 参数实现防侧信道
- ``BloggerRepository.upsert_atomic``：ON CONFLICT DO UPDATE，与 partial UNIQUE 对齐
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.models import Blogger


@dataclass(frozen=True)
class BloggerListFilters:
    """列表 / 搜索筛选条件。"""

    keyword: str | None = None
    blogger_type: str | None = None
    follower_count_min: int | None = None
    follower_count_max: int | None = None
    category_tag: str | None = None
    quality_tag: str | None = None
    platform: str | None = None
    is_suspected_fake: bool | None = None
    is_active: bool | None = True
    include_inactive: bool = False


class BloggerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ----------------------- get / count ----------------------- #

    async def get_by_id(
        self, blogger_id: UUID, *, include_deleted: bool = False
    ) -> Blogger | None:
        blogger = await self._session.get(Blogger, blogger_id)
        if blogger is None:
            return None
        if blogger.is_deleted and not include_deleted:
            return None
        return blogger

    async def get_by_xiaohongshu_id(
        self, xhs_id: str, *, include_deleted: bool = False
    ) -> Blogger | None:
        stmt = select(Blogger).where(Blogger.xiaohongshu_id == xhs_id)
        if not include_deleted:
            stmt = stmt.where(Blogger.is_deleted.is_(False))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def code_exists(self, xhs_id: str) -> bool:
        stmt = (
            select(sa.literal(1))
            .select_from(Blogger)
            .where(
                Blogger.xiaohongshu_id == xhs_id,
                Blogger.is_deleted.is_(False),
            )
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none() is not None

    async def list_active_bloggers(self, tenant_id: UUID) -> Sequence[Blogger]:
        """U11 批量重算：显式 tenant 过滤的活跃（未软删）博主全量。

        测试引擎 bypass 角色 RLS OFF，故显式 ``WHERE tenant_id``。
        """
        stmt = select(Blogger).where(
            Blogger.tenant_id == tenant_id,
            Blogger.is_deleted.is_(False),
        )
        return (await self._session.execute(stmt)).scalars().all()

    # ----------------------- list (含防侧信道) ----------------------- #

    async def list(
        self,
        *,
        filters: BloggerListFilters,
        page: int = 1,
        page_size: int = 20,
        include_wechat_in_keyword: bool = False,
    ) -> tuple[Sequence[Blogger], int]:
        """列表查询，含防侧信道：

        ``include_wechat_in_keyword=False`` 时 keyword 不匹配 wechat 字段，
        防止无 CONTACT_VISIBLE_ROLES 角色通过命中行为侧信道泄露 wechat。

        由 service 层根据 ``has_contact_visibility(role_codes)`` 决定该参数。
        """
        stmt = select(Blogger).where(Blogger.is_deleted.is_(False))

        if not filters.include_inactive:
            stmt = stmt.where(Blogger.is_active.is_(True))

        # BR-U03-50: 关键字搜索（命中 GIN trgm + 防侧信道）
        if filters.keyword:
            pattern = f"%{filters.keyword}%"
            clauses = [
                Blogger.nickname.ilike(pattern),
                Blogger.xiaohongshu_id.ilike(pattern),
            ]
            if include_wechat_in_keyword:
                clauses.append(Blogger.wechat.ilike(pattern))
            stmt = stmt.where(or_(*clauses))

        # BR-U03-51: 范围筛选
        if filters.follower_count_min is not None:
            stmt = stmt.where(Blogger.follower_count >= filters.follower_count_min)
        if filters.follower_count_max is not None:
            stmt = stmt.where(Blogger.follower_count <= filters.follower_count_max)

        # BR-U03-52: JSONB tag 包含查询（命中 GIN JSONB）
        if filters.category_tag:
            stmt = stmt.where(
                Blogger.category_tags.contains(
                    sa.cast([filters.category_tag], JSONB)
                )
            )
        if filters.quality_tag:
            stmt = stmt.where(
                Blogger.quality_tags.contains(
                    sa.cast([filters.quality_tag], JSONB)
                )
            )

        # 普通筛选
        if filters.blogger_type is not None:
            stmt = stmt.where(Blogger.blogger_type == filters.blogger_type)
        if filters.platform is not None:
            stmt = stmt.where(Blogger.platform == filters.platform)
        if filters.is_suspected_fake is not None:
            stmt = stmt.where(
                Blogger.is_suspected_fake.is_(filters.is_suspected_fake)
            )

        total_stmt = select(func.count()).select_from(stmt.subquery())
        total = int((await self._session.execute(total_stmt)).scalar_one())

        stmt = (
            stmt.order_by(
                Blogger.follower_count.desc().nulls_last(),
                Blogger.created_at.desc(),
            )
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        return items, total

    # ----------------------- upsert (P-U03-... 与 U02 P-U02-03 同模式) ----------------------- #

    async def upsert_atomic(
        self,
        *,
        tenant_id: UUID,
        values: dict[str, Any],
    ) -> tuple[Blogger, bool]:
        """``ON CONFLICT (tenant_id, xiaohongshu_id) WHERE is_deleted=false DO UPDATE``。

        Returns:
            ``(blogger, is_inserted)`` —— is_inserted=True 表示 INSERT 路径。

        通过 ``RETURNING (xmax = 0) AS is_inserted`` 判断（PostgreSQL 内部 MVCC）。

        约束：
        - ``values`` 必须包含 ``xiaohongshu_id, nickname``
        - 不更新 ``id / tenant_id / created_at / xiaohongshu_id / is_deleted``
        """
        update_fields = {
            k: v
            for k, v in values.items()
            if k
            not in {
                "id",
                "tenant_id",
                "created_at",
                "xiaohongshu_id",
                "is_deleted",
            }
        }
        update_fields["updated_at"] = sa.func.now()

        full_values = {"tenant_id": tenant_id, **values}

        stmt = pg_insert(Blogger).values(**full_values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Blogger.tenant_id, Blogger.xiaohongshu_id],
            # 谓词必须与 partial UNIQUE 索引 uq_blogger_xiaohongshu_id 完全匹配
            # （migration 用 ``is_deleted = false``）；``.is_(False)`` 生成 ``IS false``
            # 会导致 PostgreSQL "no unique constraint matching ON CONFLICT" 报错。
            index_where=sa.text("is_deleted = false"),
            set_=update_fields,
        ).returning(
            Blogger,
            sa.text("(xmax = 0) AS is_inserted"),
        )

        result = await self._session.execute(stmt)
        row = result.one()
        blogger: Blogger = row[0]
        # 混合 ORM 实体 + 原生列时按位置取（``row.is_inserted`` 在 ORM 行不可用）
        is_inserted: bool = bool(row[1])
        return blogger, is_inserted

    # ----------------------- write ----------------------- #

    def add(self, blogger: Blogger) -> None:
        self._session.add(blogger)


__all__ = ["BloggerListFilters", "BloggerRepository"]
