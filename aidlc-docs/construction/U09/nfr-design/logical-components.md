# U09 逻辑组件（Logical Components）

> 单元：U09 — 字段级权限 + 自定义权限
> 原则：新增 1 个 core 组件 + auth 3 端点；重构 4 模块；删 4 legacy；migration 012 仅 seed

---

## 1. 新建组件

| 组件 | 路径 | 职责 |
|---|---|---|
| field_permissions | `core/security/field_permissions.py` | FieldRule + FIELD_PERMISSION_REGISTRY + FieldPermissionContext + can_read_field / can_write_field / field_filter |
| PermissionService | `modules/auth/permission_service.py`（或并入 service.py） | grant / revoke / get_effective（复用 merge_permissions + 缓存失效 + audit） |
| migration 012 | `alembic/versions/012_u09_seed_field_permissions.py` | seed 字段 scope 定义（ON CONFLICT DO NOTHING），不绑角色 |

## 2. 修改组件

| 组件 | 路径 | 改动 |
|---|---|---|
| exceptions | `core/exceptions.py` | +FieldPermissionDenied（code=FIELD_PERMISSION_DENIED, http=403） |
| auth deps | `modules/auth/deps.py` | +get_field_perm_context（构造 FieldPermissionContext，带 Redis 缓存） |
| auth schemas | `modules/auth/schemas.py` | +PermissionOverrideIn（scope+reason）+EffectivePermissionsView |
| auth repository | `modules/auth/repository.py` | +upsert_override（如缺）；复用 list_codes_for_user / list_scopes_for_user |
| auth api | `modules/auth/api.py` | +3 端点（grant / revoke / effective-permissions），鉴权 auth.permission:grant |
| product service | `modules/product/service.py` | _filter_price/_check_price_writable → field_filter/can_write_field("sku",...)；删 legacy import |
| blogger service | `modules/blogger/service.py` | 过滤/写校验 → can_read/write_field("blogger",...)；search wechat 侧信道 |
| promotion service | `modules/promotion/service.py` | _check_amount_writable/to_response → can_write_field/field_filter("promotion",...) |
| finance service | `modules/finance/service.py` | payment 过滤/写 → field_filter/can_write_field("settlement",...)；proof_upload → require_permission("finance.settlement","pay") |

## 3. 删除组件

| 组件 | 原因 |
|---|---|
| `modules/product/legacy_field_permissions.py` | 统一到 core 注册表 |
| `modules/blogger/legacy_field_permissions.py` | 同上 |
| `modules/promotion/legacy_field_permissions.py` | 同上 |
| `modules/finance/legacy_field_permissions.py` | 同上 |
| 4 模块各自的 FieldPermissionDenied 重复定义 | 移至 core/exceptions.py |

## 4. 依赖图

```
core/security/field_permissions.py   (无依赖，纯常量 + 函数)
        ▲
        │ import
   ┌────┴───────────────┬──────────────┬─────────────┐
product.service   blogger.service   promotion.service  finance.service
        │                                                  │
        │ FieldPermissionContext ← auth.deps.get_field_perm_context
        ▼                                                  ▼
core/exceptions.FieldPermissionDenied (403, 全局 handler 序列化)

modules/auth/api  (3 端点)
   → PermissionService.grant/revoke/get_effective
       → repository.upsert_override + list_scopes_for_user (U01 复用)
       → domain.merge_permissions (U01 复用)
       → permissions.invalidate_user_permissions_cache (U01 复用)
       → core.audit.audit_log
```

- 无循环依赖：core 层不反向依赖 modules；4 模块单向依赖 core/security。

## 5. migration 012 字段 scope 清单（seed，不绑角色）

| scope | 说明 |
|---|---|
| field.sku.cost_price:read / :write | SKU 成本价 读/写 |
| field.sku.purchase_price:read / :write | SKU 采购价 读/写 |
| field.blogger.quote:read / :write | 博主报价 读/写 |
| field.blogger.wechat:read / :write | 博主微信 读/写 |
| field.blogger.phone:read / :write | 博主电话 读/写 |
| field.promotion.quote_amount:read / :write | 推广报价 读/写 |
| field.promotion.cost_snapshot:read / :write | 推广成本快照 读/写 |
| field.settlement.amount:read | 结算金额 读（无写 scope，状态机控制） |
| field.settlement.total_amount:read | 结算总额 读（无写 scope） |
| field.settlement.payment_amount:read / :write | 实付金额 读/写 |

> 合计 18 个 scope（settlement.amount/total_amount 仅 read）；`ON CONFLICT (scope) DO NOTHING` 幂等；downgrade 删除这些 scope；不写 role_permission。

## 6. 测试文件

| 文件 | 类型 | 覆盖 |
|---|---|---|
| `tests/unit/test_field_permissions.py` | 单元 | can_read/write_field（角色默认/grant/revoke/admin 通配/不在注册表）+ field_filter 移除 + ctx 构造 |
| `tests/integration/test_custom_permission.py` | 集成 | grant→可见 / revoke→屏蔽 / effective-permissions 结构 / 缓存失效 |
| `tests/integration/test_field_perm_regression.py` | 集成 | sku.cost_price 设计师不可见 / blogger.quote finance 只读 + wechat 侧信道 / promotion.quote_amount / settlement.payment_amount 写 403 |
| `tests/api/test_permission_api.py` | API | grant/revoke 非 admin 403 + effective 结构 + 未知 scope 422 |

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| 新建 1 core 组件 + PermissionService + migration 012 | ✅ §1 |
| 4 模块重构 + 删 4 legacy + 重复异常 | ✅ §2/§3 |
| 无循环依赖（core 单向） | ✅ §4 |
| migration 012 仅 seed 18 scope 幂等不绑角色 | ✅ §5 |
| 测试覆盖单元/集成/API + 4 模块回归 | ✅ §6 |
| 与 nfr-design-patterns P-U09-01/02 一致 | ✅ |
