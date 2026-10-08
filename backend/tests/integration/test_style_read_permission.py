"""款式下拉读权限（7a-1）：库里真实的角色权限 × 路由上真实挂的依赖。

角色取迁移 seed 的 role / role_permission（不是 default_roles.py 的内存表），
权限走 ``AuthService.load_effective_permissions``（直接读 role_permission，不走 Redis），
再把三条路由上 ``require_permission`` 生成的 checker 直接 ``await``。
这样「PR 现在点款式下拉 403」与「057 把 product.style:read 落到了库里」都能在这里复现 / 证明。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.models import Role
from app.modules.auth.service import AuthService
from app.modules.product.api import router

_DROPDOWN_ROUTES = (
    "/api/styles/",
    "/api/styles/{style_id}/goods",
    "/api/skus/by-style/{style_id}",
)

_ALLOWED = (
    "admin",
    "platform_admin",
    "designer",
    "design_assistant",
    "merchandiser",
    "operations",
    "pr",
    "pr_manager",
)
_DENIED = ("pattern_maker", "finance", "warehouse")


def _checker(path: str) -> Any:
    for route in router.routes:
        if getattr(route, "path", None) == path and "GET" in getattr(route, "methods", set()):
            return route.dependencies[0].dependency  # type: ignore[attr-defined]
    raise AssertionError(f"没找到路由 GET {path}")


async def _perms_for_role(session: AsyncSession, tenant: Any, factory: Any, code: str) -> Any:
    role = (await session.execute(select(Role).where(Role.code == code))).scalar_one()
    user = await factory.user(tenant, roles=[role])
    return await AuthService(session).load_effective_permissions(user.id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestStyleReadPermission:
    @pytest.mark.parametrize("code", _ALLOWED)
    async def test_allowed_roles_can_read_dropdowns(
        self, session: AsyncSession, tenant_a: Any, factory: Any, code: str
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            perms = await _perms_for_role(session, tenant_a, factory, code)
            for path in _DROPDOWN_ROUTES:
                # 不抛即放行
                assert await _checker(path)(perms) is perms
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize("code", _DENIED)
    async def test_denied_roles_still_403(
        self, session: AsyncSession, tenant_a: Any, factory: Any, code: str
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            perms = await _perms_for_role(session, tenant_a, factory, code)
            for path in _DROPDOWN_ROUTES:
                with pytest.raises(PermissionDeniedError) as exc_info:
                    await _checker(path)(perms)
                assert exc_info.value.status_code == 403
        finally:
            tenant_id_ctx.reset(token)

    async def test_pr_scope_seeded_by_migration(
        self, session: AsyncSession, tenant_a: Any, factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            perms = await _perms_for_role(session, tenant_a, factory, "pr")
            assert "product.style:read" in perms.scopes
            # 只开窄 scope，不连带 product 域
            assert not perms.has("product", "read")
        finally:
            tenant_id_ctx.reset(token)
