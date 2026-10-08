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
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.models import User
from app.modules.importer import access
from app.modules.importer.domain import TargetSpec, validate_mapping_config
from app.modules.importer.exceptions import (
    ImportMappingSpecUnavailableError,
    ImportMappingVersionNotFoundError,
)
from app.modules.importer.models import FieldMapping
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.repository import FieldMappingRepository
from app.modules.importer.schemas import (
    ActiveMappingInfo,
    FieldMappingCreate,
    MappingSpecResponse,
    MappingTargetItem,
)

log = logging.getLogger(__name__)


def _targets_of(source: str) -> tuple[TargetSpec, ...] | None:
    """来源 adapter 声明的映射目录；没注册或没声明 → None（按原有规则校验）。"""
    adapter = ImportAdapterRegistry.get(source)
    targets = getattr(adapter, "mapping_targets", None)
    return tuple(targets) if targets else None


class FieldMappingService:
    """字段映射版本管理。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = FieldMappingRepository(session)
        self._audit = AuditService(session)

    async def create_version(
        self, payload: FieldMappingCreate, user: User, perms: EffectivePermissions
    ) -> FieldMapping:
        """新建字段映射版本并设为 active（旧 active 同事务下线）。

        Raises:
            PermissionDeniedError(403): 没有该来源的映射权限（来源级，8a-7）。
            ImportMappingInvalidError: columns 校验失败（domain 层抛）。
        """
        access.require_mapping(perms, payload.source)

        # 1. 校验 + 规范化 columns（domain 纯函数）；有目录的来源按目录校验（8a-4）
        columns = [c.model_dump() for c in payload.columns]
        mapping_config = validate_mapping_config(columns, targets=_targets_of(payload.source))

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

    async def spec(
        self, source: str, user: User, perms: EffectivePermissions
    ) -> MappingSpecResponse:
        """映射目录 + 内置默认映射 + 当前生效版本（8a-4）。

        Raises:
            PermissionDeniedError(403): 看不到该来源。
            ImportMappingSpecUnavailableError(404): 来源没有目录。
        """
        access.require_view(perms, source)
        targets = _targets_of(source)
        adapter = ImportAdapterRegistry.get(source)
        if targets is None or adapter is None:
            raise ImportMappingSpecUnavailableError(source)
        builtin = getattr(adapter, "builtin_columns", None)
        active = await self._repo.get_active(user.tenant_id, source)
        active_info: ActiveMappingInfo | None = None
        if active is not None:
            creator = (
                await self._session.get(User, active.created_by) if active.created_by else None
            )
            active_info = ActiveMappingInfo(
                version=active.version,
                columns=list(active.mapping_config.get("columns") or []),
                created_by_name=(creator.display_name or creator.username) if creator else None,
                created_at=active.created_at,
            )
        return MappingSpecResponse(
            source=source,
            targets=[
                MappingTargetItem(
                    field=t.field,
                    label=t.label,
                    type=t.type,
                    default_col=t.default_col,
                    aliases=list(t.aliases),
                    required=t.required,
                    group=t.group,
                    create_only=t.create_only,
                )
                for t in targets
            ],
            builtin_columns=list(builtin()) if callable(builtin) else [],
            active=active_info,
        )

    async def reset(self, source: str, user: User, perms: EffectivePermissions) -> None:
        """恢复内置默认：下线生效版本（历史版本保留），之后的批次按内置默认读（8a-4）。

        没有生效版本时也成功（幂等），只是不写审计。
        """
        access.require_mapping(perms, source)
        active = await self._repo.get_active(user.tenant_id, source)
        if active is None:
            return
        await self._repo.deactivate_active(user.tenant_id, source)
        await self._audit.log(
            action="import.field_mapping.reset",
            resource="field_mapping",
            resource_id=active.id,
            before={"source": source, "version": active.version},
            after={"source": source, "active": None},
            user_id=user.id,
        )
        await self._session.commit()

    async def get_active(
        self, source: str, user: User, perms: EffectivePermissions
    ) -> FieldMapping | None:
        """取当前 active 版本（无 → None）；看不到该来源 → 403（8a-7）。"""
        access.require_view(perms, source)
        return await self._repo.get_active(user.tenant_id, source)

    async def get_by_version(self, source: str, version: int, user: User) -> FieldMapping:
        """取指定版本（不存在 → 422）。"""
        mapping = await self._repo.get_by_version(user.tenant_id, source, version)
        if mapping is None:
            raise ImportMappingVersionNotFoundError()
        return mapping

    async def list_versions(
        self, source: str, user: User, perms: EffectivePermissions
    ) -> Sequence[FieldMapping]:
        """列出某 source 的所有版本（version 倒序）；看不到该来源 → 403（8a-7）。"""
        access.require_view(perms, source)
        return await self._repo.list_versions(user.tenant_id, source)


__all__ = ["FieldMappingService"]
