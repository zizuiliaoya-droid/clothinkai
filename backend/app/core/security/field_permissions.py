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

from collections.abc import Set
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.core.security.permissions import scope_matches


@dataclass(frozen=True)
class FieldRule:
    """单个字段的默认角色权限。"""

    visible_roles: frozenset[str]
    writable_roles: frozenset[str] = frozenset()


# entity → field → FieldRule（值迁移自 4 legacy 模块，行为兼容）
FIELD_PERMISSION_REGISTRY: dict[str, dict[str, FieldRule]] = {
    # 8a-7（补充二 Q4）：运营可看可改成本价、采购价
    "sku": {
        "cost_price": FieldRule(
            frozenset({"admin", "merchandiser", "finance", "operations"}),
            frozenset({"admin", "merchandiser", "finance", "operations"}),
        ),
        "purchase_price": FieldRule(
            frozenset({"admin", "merchandiser", "finance", "operations"}),
            frozenset({"admin", "merchandiser", "finance", "operations"}),
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
        # 流程线 4.6：收件三项。财务看不到收货地址（Q4），运营看不到收件；仓库只读（仓库页与导出）
        "receiver_name": FieldRule(
            frozenset({"admin", "pr", "pr_manager", "warehouse"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
        "receiver_phone": FieldRule(
            frozenset({"admin", "pr", "pr_manager", "warehouse"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
        "receiver_address": FieldRule(
            frozenset({"admin", "pr", "pr_manager", "warehouse"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
        # 流程线 4.6 / 7d-3：收款码（含财务）。时间线的收款码类附件、推广响应里收款码 /
        # 结款凭证 / 结款聊天截图的签名都过这条
        "payment_qr": FieldRule(
            frozenset({"admin", "pr", "pr_manager", "finance"}),
            frozenset({"admin", "pr", "pr_manager"}),
        ),
    },
    # 流程线 6.5 第 2 层：时间线按事件分组过滤（键 = ``flow.line_events.EVENT_GROUP_KEYS``），只读；
    # 个人授予 / 撤销的 scope ``field.line_event.<键>:read`` 随迁移 063 入册
    "line_event": {
        "negotiation": FieldRule(frozenset({"admin", "pr", "pr_manager", "finance", "operations"})),
        "comment": FieldRule(frozenset({"admin", "pr", "pr_manager", "finance", "operations"})),
        "promotion": FieldRule(frozenset({"admin", "pr", "pr_manager", "operations"})),
        "shipping": FieldRule(frozenset({"admin", "pr", "pr_manager", "operations", "warehouse"})),
        "settlement": FieldRule(frozenset({"admin", "pr", "pr_manager", "finance", "operations"})),
        "retro": FieldRule(frozenset({"admin", "pr", "pr_manager", "operations"})),
    },
    # 流程线 4.6 / 11-17：应付、实付、调整说明加 PR 只读（推广抽屉的结款块；PR 没有 settlement scope，
    # 结款页仍进不去）。结款页自己的 amount 不变（列表「看不到金额就只看自己提交的」读的是它）
    "settlement": {
        "amount": FieldRule(frozenset({"admin", "pr_manager", "finance"})),
        "total_amount": FieldRule(frozenset({"admin", "pr_manager", "finance", "pr"})),
        "payment_amount": FieldRule(
            frozenset({"admin", "pr_manager", "finance", "pr"}),
            frozenset({"admin", "pr_manager", "finance"}),
        ),
        "payment_adjust_note": FieldRule(
            frozenset({"admin", "pr_manager", "finance", "pr"}),
            frozenset({"admin", "pr_manager", "finance"}),
        ),
    },
    # 7e R3（报表视图设计 4-F1，10-10 业务答复）：单品投产毛利三列只给管理员；
    # 持 '*' 的 admin / platform_admin 走超管判定。无 permission 行，个人授予暂不可用
    "report": {
        "production_margin": FieldRule(frozenset({"admin"})),
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
    scope = f"field.{entity}.{field}"
    if scope_matches(ctx.revokes, scope, "read"):
        return False
    if rule is None or ctx.is_superuser:
        return True
    if scope_matches(ctx.grants, scope, "read"):
        return True
    return bool(ctx.role_codes & rule.visible_roles)


def can_write_field(entity: str, field: str, ctx: FieldPermissionContext) -> bool:
    """字段写权限：撤销 > 授予 > 角色默认；不在注册表 / 超管 → True。"""
    rule = FIELD_PERMISSION_REGISTRY.get(entity, {}).get(field)
    scope = f"field.{entity}.{field}"
    if scope_matches(ctx.revokes, scope, "write"):
        return False
    if rule is None or ctx.is_superuser:
        return True
    if scope_matches(ctx.grants, scope, "write"):
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
    user_id: UUID, role_repo: Any, perm_repo: Any
) -> FieldPermissionContext:
    """构造 FieldPermissionContext（4 模块 service 共用，单一构建器）。

    Args:
        user_id: 当前用户 id。
        role_repo: RoleRepository（list_codes_for_user）。
        perm_repo: PermissionRepository（list_scopes_for_user）。

    两个 repo 参数标注为 ``Any``：它们的具体类型在 ``app.modules.auth`` 下，
    而本模块属 ``app.core``，标注真实类型会让 core 反向依赖 modules。
    """
    role_codes = frozenset(await role_repo.list_codes_for_user(user_id))
    role_scopes, grants, revokes = await perm_repo.list_scopes_for_user(user_id)
    return field_perm_context_from(role_codes, role_scopes, grants, revokes)


def field_perm_context_from(
    role_codes: frozenset[str],
    role_scopes: Set[str],
    grants: Set[str],
    revokes: Set[str],
) -> FieldPermissionContext:
    """已经查好角色码与 scope 时直接构造（流程线矩阵的当前用户与这里同一份规则）。"""
    is_superuser = ("*" in (role_scopes | grants)) and ("*" not in revokes)
    field_grants = frozenset(s for s in grants if s == "*" or s.startswith("field."))
    field_revokes = frozenset(s for s in revokes if s == "*" or s.startswith("field."))
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
    "field_perm_context_from",
    "can_write_field",
    "field_filter",
]
