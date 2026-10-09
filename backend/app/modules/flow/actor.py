"""从库里组装矩阵眼里的当前用户（``FlowActor``）：一次查角色码、一次查 scope。

有效权限与 ``AuthService.load_effective_permissions`` 同一条合并规则（撤销 > 授予 > 角色），
字段权限上下文与 ``build_field_perm_context`` 同一个构造函数，两边不会漂。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.security.field_permissions import field_perm_context_from
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.domain import merge_permissions
from app.modules.flow.matrix import FlowActor


async def load_flow_actor(user_id: UUID, role_repo: Any, perm_repo: Any) -> FlowActor:
    """``role_repo`` / ``perm_repo``：``RoleRepository`` / ``PermissionRepository``（同 ``build_field_perm_context``）。"""
    role_codes = frozenset(await role_repo.list_codes_for_user(user_id))
    role_scopes, grants, revokes = await perm_repo.list_scopes_for_user(user_id)
    return FlowActor(
        user_id=user_id,
        perms=EffectivePermissions(
            user_id=str(user_id), scopes=merge_permissions(role_scopes, grants, revokes)
        ),
        field_ctx=field_perm_context_from(role_codes, role_scopes, grants, revokes),
    )


__all__ = ["load_flow_actor"]
