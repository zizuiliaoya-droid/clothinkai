# U09 业务规则（字段级权限 + 自定义权限）

> 单元：U09 — EP01-S05 + EP01-S06
> 编号：BR-U09-NN

---

## 1. 字段权限注册表（BR-U09-01~09）

- **BR-U09-01**：`FIELD_PERMISSION_REGISTRY`（core）按 entity.field 声明默认 visible_roles / writable_roles，统一迁移 U02/U03/U04/U05 的 4 个 legacy 集合（值不变，保证行为兼容）。
- **BR-U09-02**：字段不在注册表 → 无限制（全角色可读可写），如 base_price / style_name。
- **BR-U09-03**：admin / platform_admin 持 `*` 通配 → 所有字段可读可写（不受注册表限制）。

## 2. 有效字段权限算法（BR-U09-10~13）

- **BR-U09-10**：`can_read_field(entity, field, ctx)`：撤销 `field.<e>.<f>:read` → False；授予 → True；否则 `role_codes ∩ visible_roles`。
- **BR-U09-11**：`can_write_field(entity, field, ctx)`：同上用 `:write` + writable_roles。
- **BR-U09-12**：优先级 **撤销 > 授予 > 角色默认**（与 BR-PERM-001 一致）。
- **BR-U09-13**：writable_roles 为空集（如 settlement.amount）→ 任何角色都不可通过字段写权限修改（仅状态机/系统路径可改）。

## 3. 字段读过滤（BR-U09-20~22，EP01-S06）

- **BR-U09-20**：service to_response 调 `field_filter(entity, data: dict, ctx)`：对注册表中用户不可读的字段，从响应 dict **移除**（非置 null，避免暴露字段存在性）。
- **BR-U09-21**：列表/详情/搜索响应统一过滤；嵌套对象（如 settlement.extra_item.amount）按各自 entity 过滤。
- **BR-U09-22**：未读权限字段不参与排序/keyword 匹配（防侧信道，见 BR-U09-50）。

## 4. 字段写拒绝（BR-U09-30~32，EP01-S06）

- **BR-U09-30**：写请求 payload **显式包含**不可写字段（在 `model_fields_set` 中）→ 403 `FIELD_PERMISSION_DENIED`（details 含 field）。
- **BR-U09-31**：payload 未提供该字段 → 不校验（部分更新友好）。
- **BR-U09-32**：`FIELD_PERMISSION_DENIED` 异常移到 `core/exceptions.py`（4 模块共用，删除各自重复定义）。

## 5. 自定义权限 API（BR-U09-40~44，EP01-S05）

- **BR-U09-40**：`POST /api/users/{id}/permissions/grant` body `{scope}`（含模块 scope 或 field scope）→ upsert override(effect=grant)；`auth.permission:grant` 权限（admin）。
- **BR-U09-41**：`POST /api/users/{id}/permissions/revoke` body `{scope}` → upsert override(effect=revoke)。
- **BR-U09-42**：`GET /api/users/{id}/effective-permissions` → 返回 `{scopes: [...], fields: {entity.field: {read, write}}}`（合并角色默认 + override 后的最终结果）。
- **BR-U09-43**：scope 必须是已注册 permission（permission 表存在）→ 否则 422；field scope 由 migration 012 seed。
- **BR-U09-44**：grant/revoke 后调 `invalidate_user_permissions_cache(user_id)`，立即生效。

## 6. keyword 侧信道（BR-U09-50，回归 U03 BR-U03-50）

- **BR-U09-50**：blogger keyword 搜索：仅当 `can_read_field("blogger", "wechat", ctx)` 为真时 wechat 才参与 ILIKE 匹配；否则仅匹配 nickname/xiaohongshu_id。替换原 has_contact_visibility。

## 7. 动作权限（BR-U09-60）

- **BR-U09-60**：proof_upload（付款截图上传）是**动作权限**非字段权限 → 用 scope `finance.settlement:pay`（已 seed 给 admin/finance），不入字段注册表；mark_paid 端点 require_permission 校验。

## 8. 回归（BR-U09-70~72）

- **BR-U09-70**：删除 4 个 `legacy_field_permissions.py`；product/blogger/promotion/finance service 改调 core `field_filter`/`can_write_field`。
- **BR-U09-71**：行为兼容 —— 默认角色集不变，现有字段权限测试全部回归通过（cost_price 对设计师不可见 / quote 对 finance 只读等）。
- **BR-U09-72**：新增能力 —— 通过 override 给特定用户授予/撤销字段权限（如给某 PR 授予 sku.cost_price:read）。

## 9. 多租户

- **BR-U09-80**：user_permission_override 含 tenant_id（RLS 隔离）；字段权限判定不跨租户。

---

## 10. 错误码矩阵

| 场景 | HTTP | 错误码 |
|---|---|---|
| 写不可写字段 | 403 | FIELD_PERMISSION_DENIED |
| grant/revoke 未知 scope | 422 | VALIDATION_ERROR |
| 非 admin 调 grant/revoke | 403 | PERMISSION_DENIED |
| 目标用户不存在 | 404 | RESOURCE_NOT_FOUND |

---

## 11. 性能
- 字段过滤是内存 dict 操作（注册表进程级常量）；无额外 DB 查询。
- effective-permissions 复用现有权限加载（1 次 DB + Redis 缓存）。
