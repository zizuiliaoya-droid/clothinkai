"""U06a 字段映射版本服务（EP07-S09）。

业务规则：
- 同 (tenant, source) 多版本，仅一个 ``is_active``（部分唯一约束保证）
- 新建版本：``validate_mapping_config`` → ``next_version`` → 旧 active 下线 → 插入新 active
- 旧 active 下线与新 active 插入在同一事务（原子切换，BR-U06a-26）

不涉及 R2 / Celery；纯 DB 编排，HTTP 上下文（CurrentActiveUser）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.modules.auth.models import User
from app.modules.importer.domain import validate_mapping_config
from app.modules.importer.exceptions import ImportMappingVersionNotFoundError
from app.modules.importer.models import FieldMapping
from app.modules.importer.repository import FieldMappingRepository
from app.modules.importer.schemas import FieldMappingCreate

log = logging.getLogger(__name__)


class FieldMappingService:
    """字段映射版本管理。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = FieldMappingRepository(session)
        self._audit = AuditService(session)

    async def create_version(
        self, payload: FieldMappingCreate, user: User
    ) -> FieldMapping:
        """新建字段映射版本并设为 active（旧 active 同事务下线）。

        Raises:
            ImportMappingInvalidError: columns 校验失败（domain 层抛）。
        """
        # 1. 校验 + 规范化 columns（domain 纯函数）
        columns = [c.model_dump() for c in payload.columns]
        mapping_config = validate_mapping_config(columns)

        # 2. 下一个版本号 + 旧 active 下线（同事务原子切换）
        next_version = await self._repo.next_version(user.tenant_id, payload.source)
        await self._repo.deactivate_active(user.tenant_id, payload.source)

        # 3. 插入新 active 版本
        mapping = FieldMapping(
            id=uuid4(),
            tenant_id=user.tenant_id,
            source=payload.source,
            version=next_version,
            mapping_config=mapping_config,
            is_active=True,
            created_by=user.id,
        )
        self._repo.add(mapping)
        await self._session.flush()

        await self._audit.log(
            action="import.field_mapping.create",
            resource="field_mapping",
            resource_id=mapping.id,
            after={"source": payload.source, "version": next_version},
            user_id=user.id,
        )
        await self._session.commit()
        await self._session.refresh(mapping)
        return mapping

    async def get_active(self, source: str, user: User) -> FieldMapping | None:
        """取当前 active 版本（无 → None）。"""
        return await self._repo.get_active(user.tenant_id, source)

    async def get_by_version(
        self, source: str, version: int, user: User
    ) -> FieldMapping:
        """取指定版本（不存在 → 422）。"""
        mapping = await self._repo.get_by_version(user.tenant_id, source, version)
        if mapping is None:
            raise ImportMappingVersionNotFoundError()
        return mapping

    async def list_versions(
        self, source: str, user: User
    ) -> Sequence[FieldMapping]:
        """列出某 source 的所有版本（version 倒序）。"""
        return await self._repo.list_versions(user.tenant_id, source)


__all__ = ["FieldMappingService"]
