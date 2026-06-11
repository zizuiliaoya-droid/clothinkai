"""U06a 集成测试：字段映射版本管理（EP07-S09 旧 active 下线）。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.importer.field_mapping_service import FieldMappingService
from app.modules.importer.schemas import FieldMappingColumn, FieldMappingCreate


def _payload(source: str = "fake_source") -> FieldMappingCreate:
    return FieldMappingCreate(
        source=source,
        columns=[
            FieldMappingColumn(source_col="名称", target_field="name", type="str"),
            FieldMappingColumn(
                source_col="价格", target_field="price", type="decimal"
            ),
        ],
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestFieldMappingVersioning:
    async def test_create_first_version_is_active_v1(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_manager_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            svc = FieldMappingService(session)
            m = await svc.create_version(_payload(), user)
            assert m.version == 1
            assert m.is_active is True
        finally:
            tenant_id_ctx.reset(token)

    async def test_second_version_deactivates_first(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_manager_role: Any
    ) -> None:
        """新建 v2 → v1 自动下线，仅 v2 active（部分唯一约束 + 业务逻辑）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            svc = FieldMappingService(session)
            v1 = await svc.create_version(_payload(), user)
            v2 = await svc.create_version(_payload(), user)

            assert v2.version == 2
            assert v2.is_active is True

            active = await svc.get_active("fake_source", user)
            assert active is not None
            assert active.version == 2

            refetched_v1 = await svc.get_by_version("fake_source", v1.version, user)
            assert refetched_v1.is_active is False
        finally:
            tenant_id_ctx.reset(token)

    async def test_list_versions_desc(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_manager_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[pr_manager_role])
            svc = FieldMappingService(session)
            await svc.create_version(_payload(), user)
            await svc.create_version(_payload(), user)
            versions = await svc.list_versions("fake_source", user)
            assert [v.version for v in versions] == [2, 1]
        finally:
            tenant_id_ctx.reset(token)
