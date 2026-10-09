"""8b-3 博主标签字典（设计 §5）。

存 ``dict_item``（``dict_type = 'blogger_tag'``）：表、RLS、唯一索引都现成；商品字典接口
（``product/dict_api.py``）把这个类型当保留类型挡掉。维护权 ``blogger_tag:write`` 由路由挡，
系统标签（``tag_config.SYSTEM_TAGS``）不进字典表、谁都删改不了。
"""

from __future__ import annotations

import builtins
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.modules.auth.models import User
from app.modules.blogger.exceptions import (
    BloggerTagExistsError,
    BloggerTagNotFoundError,
    BloggerTagReservedError,
)
from app.modules.blogger.tag_config import SYSTEM_TAGS
from app.modules.product.dict_models import DictItem

BLOGGER_TAG_DICT_TYPE = "blogger_tag"


class BloggerTagDictService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    async def list(self, tenant_id: UUID) -> builtins.list[DictItem]:
        """启用的字典项，按 ``sort_order, value`` 排。"""
        stmt = (
            select(DictItem)
            .where(
                DictItem.tenant_id == tenant_id,
                DictItem.dict_type == BLOGGER_TAG_DICT_TYPE,
                DictItem.is_active.is_(True),
            )
            .order_by(DictItem.sort_order.asc(), DictItem.value.asc(), DictItem.id.asc())
        )
        return builtins.list((await self._session.execute(stmt)).scalars().all())

    async def create(self, value: str, sort_order: int, user: User) -> DictItem:
        if value in SYSTEM_TAGS:
            raise BloggerTagReservedError(
                f"「{value}」是系统标签，由重算自动打，不能加进标签字典",
                details={"value": value},
            )
        stmt = (
            pg_insert(DictItem)
            .values(
                tenant_id=user.tenant_id,
                dict_type=BLOGGER_TAG_DICT_TYPE,
                value=value,
                sort_order=sort_order,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "dict_type", "value"])
            .returning(DictItem)
        )
        row = (await self._session.execute(stmt)).scalars().first()
        if row is None:
            raise BloggerTagExistsError(f"标签字典里已有「{value}」", details={"value": value})
        await self._audit.log(
            action="blogger_tag.create",
            resource="blogger_tag",
            resource_id=row.id,
            after={"value": row.value, "sort_order": row.sort_order},
            user_id=user.id,
        )
        await self._session.commit()
        return row

    async def delete(self, tag_id: UUID, user: User) -> None:
        """只删字典项，不动博主身上已有的标签。"""
        deleted = (
            await self._session.execute(
                delete(DictItem)
                .where(
                    DictItem.id == tag_id,
                    DictItem.tenant_id == user.tenant_id,
                    DictItem.dict_type == BLOGGER_TAG_DICT_TYPE,
                )
                .returning(DictItem.value, DictItem.sort_order)
            )
        ).first()
        if deleted is None:
            raise BloggerTagNotFoundError(f"标签 {tag_id} 不存在")
        await self._audit.log(
            action="blogger_tag.delete",
            resource="blogger_tag",
            resource_id=tag_id,
            before={"value": deleted.value, "sort_order": deleted.sort_order},
            user_id=user.id,
        )
        await self._session.commit()

    async def active_values(self, tenant_id: UUID, tags: Iterable[str]) -> set[str]:
        """``tags`` 里在启用字典中的那些（一条查询；导入同口径）。"""
        wanted = builtins.list(dict.fromkeys(tags))
        if not wanted:
            return set()
        rows = await self._session.execute(
            text(
                "SELECT value FROM dict_item "
                "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                "AND dict_type = CAST(:dict_type AS text) "
                "AND is_active "
                "AND value = ANY(CAST(:tags AS text[]))"
            ),
            {"tenant_id": str(tenant_id), "dict_type": BLOGGER_TAG_DICT_TYPE, "tags": wanted},
        )
        return {r[0] for r in rows}


__all__ = ["BLOGGER_TAG_DICT_TYPE", "BloggerTagDictService"]
