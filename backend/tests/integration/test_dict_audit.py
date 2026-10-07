"""8a-7：字典增删留审计（运营也能改字典，靠审计留痕；设计 §12 dict-items 行）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import AuditLog, Role
from app.modules.auth.service import AuthService
from app.modules.product.dict_api import DictItemCreate, create_dict_item, delete_dict_item


async def _audits(session: AsyncSession, action: str, item_id: str) -> list[AuditLog]:
    return list(
        (
            await session.execute(
                select(AuditLog).where(AuditLog.action == action, AuditLog.resource_id == item_id)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestDictAudit:
    async def test_operations_create_and_delete_are_audited(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            role = (
                await session.execute(select(Role).where(Role.code == "operations"))
            ).scalar_one()
            user = await factory.user(tenant_a, roles=[role])
            perms = await AuthService(session).load_effective_permissions(user.id)
            # 路由挂的是 product:write：运营现在持有（8a-7）
            assert perms.has("product", "write")

            created = await create_dict_item(
                DictItemCreate(dict_type="season", value="早春8a"), user, session
            )
            logs = await _audits(session, "dict_item.create", created.id)
            assert len(logs) == 1
            assert logs[0].resource == "dict_item"
            assert logs[0].user_id == user.id
            assert logs[0].after == {"dict_type": "season", "value": "早春8a"}

            # 已存在的再建一次：查回原记录，不算新增、不再记审计
            again = await create_dict_item(
                DictItemCreate(dict_type="season", value="早春8a"), user, session
            )
            assert again.id == created.id
            assert len(await _audits(session, "dict_item.create", created.id)) == 1

            await delete_dict_item(UUID(created.id), user, session)
            logs = await _audits(session, "dict_item.delete", created.id)
            assert len(logs) == 1
            assert logs[0].user_id == user.id
            assert logs[0].before == {"dict_type": "season", "value": "早春8a"}

            # 删一个不存在的 id：不记审计
            missing = "00000000-0000-4000-8000-0000000008a0"
            await delete_dict_item(UUID(missing), user, session)
            assert await _audits(session, "dict_item.delete", missing) == []
        finally:
            tenant_id_ctx.reset(token)
