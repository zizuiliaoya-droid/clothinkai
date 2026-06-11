# U09 技术栈决策（Tech Stack Decisions）

> 单元：U09 — 字段级权限 + 自定义权限
> 原则：复用 U01-U08 技术栈，**零新增运行时依赖 / 零新表**；migration 012 仅 permission seed

---

## 1. 依赖确认（无新增）

| 用途 | 库 | 状态 |
|---|---|---|
| 注册表 / 集合运算 | stdlib（dataclass / frozenset） | ✅ |
| 权限合并 | U01 merge_permissions / EffectivePermissions | ✅ 复用 |
| Pydantic 读模型 | pydantic 2.x | ✅ |

> requirements.txt 不改动。

---

## 2. FieldRule + 注册表（core/security/field_permissions.py）

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class FieldRule:
    visible_roles: frozenset[str]
    writable_roles: frozenset[str] = frozenset()

FIELD_PERMISSION_REGISTRY: dict[str, dict[str, FieldRule]] = {
    "sku": {
        "cost_price": FieldRule(frozenset({"admin","merchandiser","finance"}),
                                frozenset({"admin","merchandiser","finance"})),
        "purchase_price": FieldRule(frozenset({"admin","merchandiser","finance"}),
                                    frozenset({"admin","merchandiser","finance"})),
    },
    "blogger": {
        "quote": FieldRule(frozenset({"admin","pr","pr_manager","finance"}),
                           frozenset({"admin","pr","pr_manager"})),
        "wechat": FieldRule(frozenset({"admin","pr","pr_manager"}),
                            frozenset({"admin","pr","pr_manager"})),
        "phone": FieldRule(frozenset({"admin","pr","pr_manager"}),
                           frozenset({"admin","pr","pr_manager"})),
    },
    "promotion": {
        "quote_amount": FieldRule(frozenset({"admin","pr","pr_manager","finance"}),
                                  frozenset({"admin","pr","pr_manager"})),
        "cost_snapshot": FieldRule(frozenset({"admin","pr","pr_manager","finance"}),
                                   frozenset({"admin","pr","pr_manager"})),
    },
    "settlement": {
        "amount": FieldRule(frozenset({"admin","pr_manager","finance"})),
        "total_amount": FieldRule(frozenset({"admin","pr_manager","finance"})),
        "payment_amount": FieldRule(frozenset({"admin","pr_manager","finance"}),
                                    frozenset({"admin","pr_manager"})),
    },
}
```

---

## 3. FieldPermissionContext + 判定函数

```python
@dataclass(frozen=True)
class FieldPermissionContext:
    role_codes: frozenset[str]
    grants: frozenset[str]
    revokes: frozenset[str]
    is_superuser: bool   # '*' in scopes

def can_read_field(entity, field, ctx) -> bool:
    rule = FIELD_PERMISSION_REGISTRY.get(entity, {}).get(field)
    if rule is None or ctx.is_superuser:
        return True
    scope = f"field.{entity}.{field}:read"
    if scope in ctx.revokes:
        return False
    if scope in ctx.grants:
        return True
    return bool(ctx.role_codes & rule.visible_roles)

def can_write_field(entity, field, ctx) -> bool:
    rule = FIELD_PERMISSION_REGISTRY.get(entity, {}).get(field)
    if rule is None or ctx.is_superuser:
        return True
    scope = f"field.{entity}.{field}:write"
    if scope in ctx.revokes:
        return False
    if scope in ctx.grants:
        return True
    return bool(ctx.role_codes & rule.writable_roles)

def field_filter(entity, data: dict, ctx) -> dict:
    for field in FIELD_PERMISSION_REGISTRY.get(entity, {}):
        if field in data and not can_read_field(entity, field, ctx):
            data.pop(field)        # 移除，非 null
    return data
```

- 优先级 撤销 > 授予 > 角色（与 BR-PERM-001 merge_permissions 一致）。
- `FieldPermissionDenied` 移到 `core/exceptions.py`（4 模块共用）。

---

## 4. 复用 U01 权限合并

- `list_scopes_for_user(user_id)` → (role_scopes, grants, revokes)（已含 field scope）。
- service 入口构造 ctx：`role_codes`=RoleRepository.list_codes_for_user；`grants/revokes`=override 的 field scope 子集；`is_superuser`=`"*" in merged_scopes`。
- grant/revoke 复用 `user_permission_override` upsert + `invalidate_user_permissions_cache`。

---

## 5. migration 012（仅 permission seed，幂等）

```python
# 012_u09_seed_field_permissions.py（接 011）
FIELD_SCOPES = [
    ("field.sku.cost_price:read", "字段-SKU成本价读", "field"),
    ("field.sku.cost_price:write", "字段-SKU成本价写", "field"),
    # ... 全部注册表字段的 read/write + base_price 不含
]
# INSERT INTO permission ... ON CONFLICT (scope) DO NOTHING
# 不写 role_permission（默认按注册表角色判定）
```

无新表 / 无 DDL；downgrade 删除这些 scope。

---

## 6. 4 模块回归落点

| 模块 | 改动 |
|---|---|
| product/service.py | _filter_price / _check_price_writable → field_filter / can_write_field；删 import legacy |
| blogger/service.py | _filter / _check + search keyword → can_read/write_field |
| promotion/service.py | _check_amount_writable + to_response → can_write_field / field_filter |
| finance/service.py | payment 过滤/写 → field_filter / can_write_field；proof_upload → require_permission("finance.settlement","pay") |
| 删除 | 4 个 legacy_field_permissions.py + 各模块 FieldPermissionDenied 重复定义 |
| auth | PermissionService（grant/revoke/get_effective）+ api 3 端点 + deps |

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ §1 |
| FieldRule 注册表（值迁移自 4 legacy） | ✅ §2 |
| can_read/write_field 撤销>授予>角色 | ✅ §3 |
| 复用 merge_permissions + 缓存失效 | ✅ §4 |
| migration 012 仅 permission seed 幂等 | ✅ §5 |
| 4 模块回归 + 删 legacy | ✅ §6 |
