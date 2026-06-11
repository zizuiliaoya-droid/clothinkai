"""U09 集成测试：自定义权限 grant/revoke/effective + 字段级 override 生效。

覆盖 EP01-S05 + EP01-S06：
- grant field scope → 字段对原本无权限角色可见
- revoke → 字段屏蔽（撤销优先级最高）
- get_effective 结构
- 未知 scope → ValidationError(422)
- 用户不存在 → ResourceNotFoundError(404)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError, ValidationError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Permission
from app.modules.auth.service import PermissionService
from app.modules.product.service import SkuService

_COST_READ = "field.sku.cost_price:read"


@pytest.fixture(autouse=True)
def _stub_cache(monkeypatch: Any) -> None:
    """Stub Redis cache（避免真实 redis 跨事件循环绑定问题，与既有测试一致）。"""
    from unittest.mock import AsyncMock

    stub = AsyncMock()
    monkeypatch.setattr("app.core.security.permissions.cache", stub)
    monkeypatch.setattr("app.modules.auth.service.cache", stub)


async def _ensure_perm(session: AsyncSession, scope: str) -> Permission:
    existing = (
        await session.execute(select(Permission).where(Permission.scope == scope))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    perm = Permission(id=uuid4(), scope=scope, name=scope, category="field")
    session.add(perm)
    await session.flush()
    return perm


@pytest.mark.integration
@pytest.mark.asyncio
class TestCustomFieldPermission:
    async def test_grant_makes_field_visible(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _ensure_perm(session, _COST_READ)
            style = await product_factory.style()
            sku = await product_factory.sku(style, cost_price=Decimal("100.00"))
            pr_user = await factory.user(tenant_a, roles=[pr_role])
            admin = await factory.user(tenant_a, roles=[admin_role])

            svc = SkuService(session)
            # baseline：pr 不可见 cost_price
            assert (await svc.get_sku(sku.id, pr_user)).cost_price is None

            # grant field.sku.cost_price:read 给该 pr 用户
            await PermissionService(session).grant(
                pr_user.id, _COST_READ, actor_id=admin.id, reason="临时核价"
            )
            # 现在可见
            assert (await svc.get_sku(sku.id, pr_user)).cost_price == Decimal("100.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_revoke_hides_field(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        finance_role: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _ensure_perm(session, _COST_READ)
            style = await product_factory.style()
            sku = await product_factory.sku(style, cost_price=Decimal("88.00"))
            fin_user = await factory.user(tenant_a, roles=[finance_role])
            admin = await factory.user(tenant_a, roles=[admin_role])

            svc = SkuService(session)
            # finance 默认可见
            assert (await svc.get_sku(sku.id, fin_user)).cost_price == Decimal("88.00")

            # revoke → 撤销优先级最高 → 屏蔽
            await PermissionService(session).revoke(
                fin_user.id, _COST_READ, actor_id=admin.id
            )
            assert (await svc.get_sku(sku.id, fin_user)).cost_price is None
        finally:
            tenant_id_ctx.reset(token)

    async def test_get_effective_structure(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _ensure_perm(session, _COST_READ)
            pr_user = await factory.user(tenant_a, roles=[pr_role])
            admin = await factory.user(tenant_a, roles=[admin_role])
            psvc = PermissionService(session)
            await psvc.grant(pr_user.id, _COST_READ, actor_id=admin.id)

            view = await psvc.get_effective(pr_user.id)
            assert view["user_id"] == str(pr_user.id)
            assert _COST_READ in view["grants"]
            assert _COST_READ in view["effective"]
            assert isinstance(view["revokes"], list)
        finally:
            tenant_id_ctx.reset(token)

    async def test_unknown_scope_rejected(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr_user = await factory.user(tenant_a, roles=[pr_role])
            admin = await factory.user(tenant_a, roles=[admin_role])
            with pytest.raises(ValidationError):
                await PermissionService(session).grant(
                    pr_user.id, "field.bogus.x:read", actor_id=admin.id
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_grant_to_missing_user(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await _ensure_perm(session, _COST_READ)
            admin = await factory.user(tenant_a, roles=[admin_role])
            with pytest.raises(ResourceNotFoundError):
                await PermissionService(session).grant(
                    uuid4(), _COST_READ, actor_id=admin.id
                )
        finally:
            tenant_id_ctx.reset(token)
