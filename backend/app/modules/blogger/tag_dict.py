"""8b-3 博主标签字典（设计 §5）。

存 ``dict_item``（``dict_type = 'blogger_tag'``）：表、RLS、唯一索引都现成；商品字典接口
（``product/dict_api.py``）把这个类型当保留类型挡掉。维护权 ``blogger_tag:write`` 由路由挡，
系统标签（``tag_config.SYSTEM_TAGS``）不进字典表、谁都删改不了。

导入缺的标签（§6.6，D2）：导入时字典外的类目标签被丢掉、整批只提示一次；
``missing_tags_for_batch`` 读时现算该批缺了哪些标签，主管补完字典清单就变短。
"""

from __future__ import annotations

import builtins
from collections.abc import Iterable
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.models import User
from app.modules.blogger.exceptions import (
    BloggerTagExistsError,
    BloggerTagNotFoundError,
    BloggerTagReservedError,
)
from app.modules.blogger.tag_config import SYSTEM_TAGS
from app.modules.importer import access
from app.modules.importer.exceptions import ImportBatchNotFoundError
from app.modules.importer.models import ImportBatch, ImportJob
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.repository import FieldMappingRepository
from app.modules.product.dict_models import DictItem

BLOGGER_TAG_DICT_TYPE = "blogger_tag"

# 不指定批次时取这个来源最近的一批（类目标签只有博主模版有）
_DEFAULT_MISSING_SOURCE = "manual_blogger"
# 每个缺的标签最多带回的行号个数
MISSING_ROWS_LIMIT = 20


@dataclass
class MissingTag:
    tag: str
    count: int  # 含这个标签的行数（同一行重复只算一次）
    rows: list[int] = field(default_factory=list)  # 前 MISSING_ROWS_LIMIT 个行号，升序


@dataclass
class MissingTagsResult:
    batch_id: UUID | None  # None = 没有看得到的博主导入批次
    items: list[MissingTag] = field(default_factory=list)


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

    async def missing_tags_for_batch(
        self, tenant_id: UUID, perms: EffectivePermissions, batch_id: UUID | None
    ) -> MissingTagsResult:
        """该批导入里不在**当前**启用字典的类目标签（§6.6）。

        读批次里非失败行的 ``import_job.raw_data``，按该批的映射版本过 adapter 的 ``parse_row``
        取类目标签，减去当前启用字典与系统标签，按标签计行数、记前 20 个行号；按次数降序、标签升序。
        只返回标签与行号（不带别的列，不涉及受保护字段）。

        ``batch_id`` 不给 → 本租户最近一个 ``manual_blogger`` 批次（看不到该来源或还没有 → 空结果）。
        给了但不存在 / 别的租户 / 不是博主来源 / 看不到 → 404（不暴露存在性）。
        """
        # 延迟导入：adapters.blogger 本身引用本模块（active_values）
        from app.modules.importer.adapters.blogger import BloggerImportAdapter

        if batch_id is None:
            if not access.can_view(perms, _DEFAULT_MISSING_SOURCE):
                return MissingTagsResult(batch_id=None)
            batch = (
                await self._session.execute(
                    select(ImportBatch)
                    .where(
                        ImportBatch.tenant_id == tenant_id,
                        ImportBatch.source == _DEFAULT_MISSING_SOURCE,
                    )
                    .order_by(ImportBatch.created_at.desc(), ImportBatch.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if batch is None:
                return MissingTagsResult(batch_id=None)
        else:
            batch = await self._session.get(ImportBatch, batch_id)
            if (
                batch is None
                or batch.tenant_id != tenant_id
                or not access.can_view(perms, batch.source)
            ):
                raise ImportBatchNotFoundError(batch_id)
        adapter = ImportAdapterRegistry.get(batch.source)
        if not isinstance(adapter, BloggerImportAdapter):
            raise ImportBatchNotFoundError(batch.id)

        mapping = None
        if batch.mapping_version is not None:
            mapping = await FieldMappingRepository(self._session).get_by_version(
                tenant_id, batch.source, batch.mapping_version
            )
        jobs = await self._session.execute(
            select(ImportJob.row_number, ImportJob.raw_data)
            .where(
                ImportJob.tenant_id == tenant_id,
                ImportJob.batch_id == batch.id,
                ImportJob.status != "failed",
            )
            .order_by(ImportJob.row_number.asc())
        )
        rows_by_tag: dict[str, builtins.list[int]] = {}
        for row_number, raw in jobs:
            tags = adapter.parse_row(dict(raw), mapping).get("category_tags") or []
            for tag in dict.fromkeys(tags):  # 同一行重复只算一次
                rows_by_tag.setdefault(tag, []).append(int(row_number))

        known = await self.active_values(tenant_id, rows_by_tag)
        items = [
            MissingTag(tag=tag, count=len(rows), rows=rows[:MISSING_ROWS_LIMIT])
            for tag, rows in rows_by_tag.items()
            if tag not in known and tag not in SYSTEM_TAGS
        ]
        items.sort(key=lambda i: (-i.count, i.tag))
        return MissingTagsResult(batch_id=batch.id, items=items)


__all__ = [
    "BLOGGER_TAG_DICT_TYPE",
    "MISSING_ROWS_LIMIT",
    "BloggerTagDictService",
    "MissingTag",
    "MissingTagsResult",
]
