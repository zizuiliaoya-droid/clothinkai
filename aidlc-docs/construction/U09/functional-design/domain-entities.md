# U09 领域实体（字段级权限 + 自定义权限）

> 单元：U09 — 字段级权限 + 自定义权限（EP01-S05 + S06）
> 依赖：U01（RBAC + user_permission_override + merge_permissions）
> 特征：**无新表**（复用 U01 user_permission_override + permission）；核心是 core 字段权限注册表 + 字段 scope

---

## 1. 实体总览

| 对象 | 类型 | 来源 |
|---|---|---|
| FieldRule | 代码常量（dataclass） | U09 新建 `core/security/field_permissions.py` |
| FIELD_PERMISSION_REGISTRY | dict[entity → dict[field → FieldRule]] | U09 新建 |
| user_permission_override | ORM（已存在） | U01；U09 复用承载字段级自定义授予/撤销 |
| permission | ORM（已存在） | U01；U09 migration 012 seed 字段 scope 定义 |
| EffectivePermissions | dataclass（已存在） | U01；scopes 已可含 field scope |

> U09 不新增 ORM 表 / migration 仅做 permission seed（字段 scope 定义，不绑角色）。

---

## 2. FieldRule（字段权限规则，代码常量）

```python
@dataclass(frozen=True)
class FieldRule:
    visible_roles: frozenset[str]   # 默认可读角色
    writable_roles: frozenset[str]  # 默认可写角色（⊆ visible 语义上）
```

`FIELD_PERMISSION_REGISTRY: dict[str, dict[str, FieldRule]]`（entity → field → rule），
统一迁移 4 个 legacy_field_permissions：

| entity | field | visible_roles | writable_roles |
|---|---|---|---|
| sku | cost_price | admin, merchandiser, finance | admin, merchandiser, finance |
| sku | purchase_price | admin, merchandiser, finance | admin, merchandiser, finance |
| blogger | quote | admin, pr, pr_manager, finance | admin, pr, pr_manager |
| blogger | wechat | admin, pr, pr_manager | admin, pr, pr_manager |
| blogger | phone | admin, pr, pr_manager | admin, pr, pr_manager |
| promotion | quote_amount | admin, pr, pr_manager, finance | admin, pr, pr_manager |
| promotion | cost_snapshot | admin, pr, pr_manager, finance | admin, pr, pr_manager |
| settlement | amount | admin, pr_manager, finance | （只读，无写） |
| settlement | total_amount | admin, pr_manager, finance | （只读，无写） |
| settlement | payment_amount | admin, pr_manager, finance | admin, pr_manager |

> `base_price` 等全角色字段不入注册表（不在注册表 = 无限制）。
> settlement.amount/total_amount 写由状态机控制，非字段写权限（注册表 writable 留空）。

---

## 3. 字段 scope 命名（自定义 override 用）

- 读：`field.<entity>.<field>:read`（如 `field.sku.cost_price:read`）
- 写：`field.<entity>.<field>:write`
- 自定义授予/撤销复用 `user_permission_override`（effect=grant/revoke），与 scope 级权限同一张表、同一 merge 算法。

---

## 4. 有效字段权限算法

```
can_read_field(entity, field, ctx):
    rule = REGISTRY[entity].get(field)
    if rule is None:                       # 不在注册表 → 无限制
        return True
    scope = f"field.{entity}.{field}:read"
    if scope in ctx.revokes:               # 自定义撤销优先级最高
        return False
    if scope in ctx.grants:                # 自定义授予
        return True
    return bool(ctx.role_codes & rule.visible_roles)   # 默认按角色

can_write_field(entity, field, ctx):  # 同上，用 writable_roles + :write scope
```

优先级：**自定义撤销 > 自定义授予 > 角色默认**（与 BR-PERM-001 merge_permissions 一致）。
admin / platform_admin 持 `*` 通配 → 所有字段可读可写。

---

## 5. 自定义权限（EP01-S05，复用 U01）

| 对象 | 说明 |
|---|---|
| user_permission_override | (tenant_id, user_id, permission_id, effect, reason, created_by)；UNIQUE(tenant, user, permission) |
| effect | grant / revoke |
| merge_permissions | (role_scopes ∪ grants) − revokes（已实现，含 field scope） |

EP01-S05 新增能力 = **API 端点**（grant/revoke/effective），底层数据 + 合并算法 U01 已就绪。

---

## 6. EffectivePermissions（已存在，U09 扩展语义）

`scopes: frozenset[str]` 现含：模块 scope（`promotion.*:*`）+ 字段 scope（`field.sku.cost_price:read`，仅自定义 override 才出现）。`has(scope, action)` 通配匹配不变。

字段判定额外结合 `role_codes`（注册表默认）→ U09 在 service/ctx 传入 role_codes + EffectivePermissions 的 grants/revokes 子集。

---

## 7. 回归契约（删除 4 legacy 模块）

| 模块 | 原 legacy 函数 | U09 替换 |
|---|---|---|
| product | has_price_visibility | can_read_field("sku", "cost_price", ctx) |
| blogger | has_quote/contact_visibility + has_quote_writable | can_read_field/can_write_field("blogger", ...) |
| promotion | has_amount_visibility/writable | can_read/write_field("promotion", ...) |
| finance | has_payment_visibility/writable + has_proof_upload | can_read/write_field("settlement", ...)；proof_upload 保留为 scope 权限（finance.settlement:pay） |

> proof_upload（PROOF_UPLOAD_ROLES）是**动作权限**非字段权限 → 改用 scope `finance.settlement:pay`（已 seed 给 finance/admin），不入字段注册表。

---

## 8. 演化
- V2：字段 scope 可在管理界面可视化配置（当前 grant/revoke 走 API）。
- 注册表未来可迁移 DB 字典（MVP/V1 代码常量足够）。
