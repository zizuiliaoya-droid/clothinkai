# U09 业务逻辑模型（字段级权限 + 自定义权限）

> 单元：U09 — EP01-S05 + EP01-S06
> UC + 字段权限上下文 + 与 U01 merge_permissions 契约 + 4 模块回归映射

---

## 字段权限上下文（FieldPermissionContext）

```
ctx = {
  role_codes: set[str],         # 当前用户角色 code（RoleRepository.list_codes_for_user）
  grants: set[str],             # 自定义 grant scope（含 field scope）
  revokes: set[str],            # 自定义 revoke scope
  is_superuser: bool,           # '*' in scopes（admin/platform_admin）
}
```
由 service 在请求入口构造一次，传给 field_filter / can_write_field 复用。

---

## UC-1 字段读过滤（EP01-S06，所有含敏感字段的响应）

```
service.to_response(entity_obj, ctx):
  data = serialize(entity_obj)            # dict
  field_filter("sku", data, ctx)          # core/security/field_permissions
    for field in REGISTRY["sku"]:
        if not can_read_field("sku", field, ctx):
            data.pop(field, None)          # 移除（非 null）
  return SkuResponse(**data)
```

示例：设计师（role=designer，无 sku.cost_price 默认）GET /api/skus/{id}
→ cost_price / purchase_price 从响应移除（BR-U09-20）。

---

## UC-2 字段写拒绝（EP01-S06）

```
service.update_sku(sku_id, payload, ctx):
  for field in payload.model_fields_set:
      if field in REGISTRY["sku"] and not can_write_field("sku", field, ctx):
          raise FieldPermissionDenied(field)        # 403（BR-U09-30）
  ... 正常更新
```

设计师 PUT cost_price → 403 FIELD_PERMISSION_DENIED；未提供 cost_price → 放行。

---

## UC-3 授予自定义权限（EP01-S05）

```
[admin] POST /api/users/{id}/permissions/grant {scope: "field.sku.cost_price:read"}
  → @require_permission("auth.permission", "grant")
  → PermissionService.grant(user_id, scope)
        ├── perm = permission.get_by_scope(scope) else 422        # BR-U09-43
        ├── upsert user_permission_override(user_id, perm.id, effect="grant")
        ├── invalidate_user_permissions_cache(user_id)            # BR-U09-44
        └── audit("permission.grant")
  → 200
```

revoke 同理（effect="revoke"）。撤销优先级最高（BR-U09-12）。

---

## UC-4 查询有效权限（EP01-S05）

```
[admin] GET /api/users/{id}/effective-permissions
  → PermissionService.get_effective(user_id)
        ├── role_scopes, grants, revokes = list_scopes_for_user(user_id)
        ├── scopes = merge_permissions(role_scopes, grants, revokes)   # U01
        ├── role_codes = roles.list_codes_for_user(user_id)
        └── fields = { f"{e}.{f}": {
                "read": can_read_field(e, f, ctx),
                "write": can_write_field(e, f, ctx),
              } for e in REGISTRY for f in REGISTRY[e] }
  → 200 { scopes: [...], fields: {...} }
```

---

## UC-5 keyword 侧信道防护（回归 U03 BR-U03-50）

```
blogger.search(filters, ctx):
  match_wechat = can_read_field("blogger", "wechat", ctx)     # BR-U09-50
  if filters.keyword:
      conds = [nickname ILIKE :kw, xiaohongshu_id ILIKE :kw]
      if match_wechat: conds.append(wechat ILIKE :kw)
      where OR(conds)
```

---

## 4 模块回归映射

| 模块 | 原调用 | U09 替换 |
|---|---|---|
| product.service._filter_price | has_price_visibility(roles) | field_filter("sku", data, ctx) |
| product.service._check_price_writable | PRICE_VISIBLE_ROLES 判断 | can_write_field("sku", field, ctx) |
| blogger.service._filter / _check | has_quote/contact_visibility | field_filter("blogger", ...) / can_write_field |
| blogger.search keyword | has_contact_visibility | can_read_field("blogger","wechat",ctx) |
| promotion.service._check_amount_writable | has_amount_writable | can_write_field("promotion", ...) |
| promotion.to_response | has_amount_visibility | field_filter("promotion", ...) |
| finance.service payment 过滤/写 | has_payment_visibility/writable | field_filter("settlement",...) / can_write_field |
| finance proof_upload | has_proof_upload | require_permission("finance.settlement","pay") |

删除：4 个 legacy_field_permissions.py + 各模块重复 FieldPermissionDenied（移 core）。

---

## 跨单元契约

| 契约 | 提供方 | 使用 |
|---|---|---|
| user_permission_override + merge_permissions + list_scopes_for_user | U01 | 自定义权限 + 字段 scope 叠加 |
| invalidate_user_permissions_cache | U01 | grant/revoke 后失效 |
| RoleRepository.list_codes_for_user | U01 | ctx.role_codes |
| FIELD_PERMISSION_REGISTRY + field_filter/can_read/write_field | U09 core | 4 模块 + report 复用 |

---

## 故事覆盖校验

| 故事 | UC | 状态 |
|---|---|---|
| EP01-S05 自定义授予/撤销 | UC-3 | ✅ |
| EP01-S05 effective-permissions | UC-4 | ✅ |
| EP01-S06 字段读屏蔽 | UC-1 | ✅ |
| EP01-S06 字段写 403 | UC-2 | ✅ |
| 回归（4 模块行为兼容 + keyword 侧信道） | UC-5 + 回归映射 | ✅ |
