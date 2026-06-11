"""U09 字段级权限统一注册表 + 判定 + 过滤。

统一 U02-U05 的 4 个 ``legacy_field_permissions`` 模块（角色硬编码）为单一
core 注册表，并叠加字段级自定义 override（撤销 > 授予 > 角色默认，与 BR-PERM-001
``merge_permissions`` 语义一致）。

字段 scope 命名：``field.<entity>.<field>:read`` / ``field.<entity>.<field>:write``。

设计要点：
- ``FIELD_PERMISSION_REGISTRY`` 为进程级常量（import 时构建），零运行时构建开销。
- 判定为内存 dict/set 运算 O(字段数)，无额外 DB。
- ``field_filter`` 从 dict 移除不可读字段（移除而非置 null，防存在性泄露）；
  既有 4 模块 Pydantic 响应为回归兼容保留 None 投影，新增/字典型响应可用本函数。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldRule:
    """单个字段的默认角色权限。"""

    visible_roles: frozenset[str]
    writable_roles: frozenset[str] = frozenset()


# entity → field → FieldRule（值迁移自 4 legacy 模块，行为兼容）
FIELD_PERMISSION_REGISTRY: dict[str, dict[str, FieldRule]] = {
    "sku": {
        "cost_price": FieldRule(
            frozenset({"admin", "merchandiser", "finance"}),
            frozenset({"admin", "merchandiser", "finance"}),
        ),
        "purchase_price": FieldRule(
            frozenset({"admin", "merchandiser", "finance"}),
            frozenset({"admin", "merchandiser", "finance"}),
        ),
    },
    "blogger": {
        "quote": FieldRule(
            frozenset({"admin", "pr", "pr_manager", "finance"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
        "wechat": FieldRule(
            frozenset({"admin", "pr", "pr_manager"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
        "phone": FieldRule(
            frozenset({"admin", "pr", "pr_manager"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
    },
    "promotion": {
        "quote_amount": FieldRule(
            frozenset({"admin", "pr", "pr_manager", "finance"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
        "cost_snapshot": FieldRule(
            frozenset({"admin", "pr", "pr_manager", "finance"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
    },
    "settlement": {
        "amount": FieldRule(frozenset({"admin", "pr_manager", "finance"})),
        "total_amount": FieldRule(frozenset({"admin", "pr_manager", "finance"})),
        "payment_amount": FieldRule(
            frozenset({"admin", "pr_manager", "finance"}),
            frozenset({"admin", "pr_manager"}),
        ),
    },
}


@dataclass(frozen=True)
class FieldPermissionContext:
    """字段权限判定上下文（每请求/每用户构造一次）。"""

    role_codes: frozenset[str]
    grants: frozenset[str]  # field. 前缀的自定义授予 scope
    revokes: frozenset[str]  # field. 前缀的自定义撤销 scope
    is_superuser: bool = False  # '*' 通配（admin / platform_admin）


def can_read_field(entity: str, field: str, ctx: FieldPermissionContext) -> bool:
    """字段读权限：撤销 > 授予 > 角色默认；不在注册表 / 超管 → True。"""
    rule = FIELD_PERMISSION_REGISTRY.get(entity, {}).get(field)
    if rule is None or ctx.is_superuser:
        return True
    scope = f"field.{entity}.{field}:read"
    if scope in ctx.revokes:
        return False
    if scope in ctx.grants:
        return True
    return bool(ctx.role_codes & rule.visible_roles)


def can_write_field(entity: str, field: str, ctx: FieldPermissionContext) -> bool:
    """字段写权限：撤销 > 授予 > 角色默认；不在注册表 / 超管 → True。"""
    rule = FIELD_PERMISSION_REGISTRY.get(entity, {}).get(field)
    if rule is None or ctx.is_superuser:
        return True
    scope = f"field.{entity}.{field}:write"
    if scope in ctx.revokes:
        return False
    if scope in ctx.grants:
        return True
    return bool(ctx.role_codes & rule.writable_roles)


def field_filter(entity: str, data: dict, ctx: FieldPermissionContext) -> dict:
    """从响应 dict 移除不可读字段（移除而非置 null，防存在性泄露）。

    供新增/字典型响应使用；既有 4 模块 Pydantic 响应为回归兼容保留 None 投影。
    """
    for field in FIELD_PERMISSION_REGISTRY.get(entity, {}):
        if field in data and not can_read_field(entity, field, ctx):
            data.pop(field)
    return data


async def build_field_perm_context(
    user_id, role_repo, perm_repo
) -> FieldPermissionContext:
    """构造 FieldPermissionContext（4 模块 service 共用，单一构建器）。

    Args:
        user_id: 当前用户 id。
        role_repo: RoleRepository（list_codes_for_user）。
        perm_repo: PermissionRepository（list_scopes_for_user）。
    """
    role_codes = frozenset(await role_repo.list_codes_for_user(user_id))
    role_scopes, grants, revokes = await perm_repo.list_scopes_for_user(user_id)
    is_superuser = ("*" in (role_scopes | grants)) and ("*" not in revokes)
    field_grants = frozenset(s for s in grants if s.startswith("field."))
    field_revokes = frozenset(s for s in revokes if s.startswith("field."))
    return FieldPermissionContext(
        role_codes=role_codes,
        grants=field_grants,
        revokes=field_revokes,
        is_superuser=is_superuser,
    )


__all__ = [
    "FIELD_PERMISSION_REGISTRY",
    "FieldPermissionContext",
    "FieldRule",
    "build_field_perm_context",
    "can_read_field",
    "can_write_field",
    "field_filter",
]
