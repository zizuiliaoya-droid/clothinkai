# U09 代码生成计划（Code Generation Plan）

> 单元：U09 — 字段级权限 + 自定义权限
> 分批：4 批 + Build & Test；统一 4 legacy → core 注册表 + 字段级自定义 override + 3 自定义权限 API
> Build & Test：Docker PG16:5551 + Redis7:6406 + Py3.12

---

## 0. 关键设计修订（基于现有测试约束）

- **响应表示保持 None 投影**：现有 U02/U03/U04/U05 测试断言 `response.cost_price is None` / `response.wechat is None`（Pydantic 模型字段存在且为 None）。为保证回归全绿 + 前端契约不变，4 模块既有 Pydantic 响应**继续用 `X if can_read_field(...) else None` 投影**（不改为移除 key）。
- `field_filter`（dict 移除语义）仍在 core 实现，供新增/字典型响应与 effective-permissions 使用；既有 4 模块不强制切换 dict。
- **决策来源统一**：所有读可见 / 写可写判定改为经 core `can_read_field` / `can_write_field`（注册表默认 + 字段级 grant/revoke override），不再用 4 legacy 的 `has_*`。
- proof_upload → `finance.settlement:pay`（scope 已存在 default_roles：finance 直接持有 + admin 通配；pr_manager 无 → 与 PROOF_UPLOAD_ROLES={admin,finance} 一致）。

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 响应移除 vs None
- [Answer] 见 §0：保持 None 投影（回归兼容）；field_filter 供新场景。

### Q2 — ctx 构造单一来源
- [Answer] core `build_field_perm_context(user_id, role_repo, perm_repo)` 单一构建器，4 模块共用；role_codes ← list_codes_for_user；grants/revokes ← list_scopes_for_user 的 field. 前缀子集；is_superuser ← '*' in (role_scopes∪grants) and '*' not in revokes。

### Q3 — FieldPermissionDenied 兼容
- [Answer] 移至 core/exceptions.py，签名 `__init__(self, field, entity=None)` 向后兼容既有 `FieldPermissionDenied(field=...)`；product/exceptions.py 改为 re-export core（blogger/promotion/finance 经 product re-export 不变）。

### Q4 — proof_upload
- [Answer] finance service `_check_proof_upload_permission` 改用 EffectivePermissions.has("finance.settlement","pay")（merge_permissions 构造），删 has_proof_upload。

### Q5 — migration 012
- [Answer] seed 18 字段 scope + auth.permission:grant（category 'auth'）；ON CONFLICT DO NOTHING；不绑角色（admin '*' 通配）。

### Q6 — extra_item 写权限
- [Answer] finance `_check_extra_item` 原用 has_extra_item_writable（=PAYMENT_WRITABLE admin/pr_manager）→ 改 can_write_field("settlement","payment_amount",ctx)（writable_roles=admin/pr_manager 一致）。

### Q7 — 缓存
- [Answer] grant/revoke 后失效 perm:user:<id> + fieldctx:user:<id>。

### Q8 — 测试端口
- [Answer] Docker PG16:5551 + Redis7:6406。

---

## 2. 批次步骤

### Batch 1 — Core 基础（本批）
- [x] 1.1 `core/security/field_permissions.py`（新建）：FieldRule + FIELD_PERMISSION_REGISTRY（10 字段）+ FieldPermissionContext + can_read_field + can_write_field + field_filter + build_field_perm_context
- [x] 1.2 `core/exceptions.py`：+FieldPermissionDenied（field+entity，code=FIELD_PERMISSION_DENIED，403）
- [x] 1.3 `modules/product/exceptions.py`：FieldPermissionDenied 改为 from core re-export（保留 __all__）

### Batch 2 — 4 模块 service 重构 + 删 legacy
- [x] 2.1 product/service.py（SkuService _check_price_write_permission + _to_response 用 can_write/read_field("sku",...)；删 legacy import）
- [x] 2.2 blogger/service.py（_check_sensitive_write_permission + _to_response + list_bloggers wechat 侧信道 → can_*_field("blogger",...)；删 legacy import）
- [x] 2.3 promotion/service.py（_check_amount_write_permission + _to_response → can_*_field("promotion",...)；删 legacy import）
- [x] 2.4 finance/service.py（payment 可见/可写 + extra_item + proof_upload(scope) + daily_summary → can_*_field("settlement",...) / EffectivePermissions；删 legacy import）
- [x] 2.5 删除 4 个 legacy_field_permissions.py

### Batch 3 — auth 自定义权限 API
- [x] 3.1 repository.py：PermissionRepository.upsert_override（UNIQUE 冲突更新 effect/reason）
- [x] 3.2 schemas.py：PermissionOverrideIn（scope+reason）+ EffectivePermissionsView
- [x] 3.3 service.py：PermissionService（grant/revoke/get_effective 复用 merge_permissions + 双缓存失效 + audit + 未知 scope 422）
- [x] 3.4 deps.py：get_field_perm_context（可选，供 API 层）；api.py：3 端点（grant/revoke/effective）鉴权 auth.permission:grant

### Batch 4 — migration 012 + 测试
- [x] 4.1 `alembic/versions/012_u09_seed_field_permissions.py`（18 字段 scope + auth.permission:grant，幂等）
- [x] 4.2 tests/unit/test_field_permissions.py（can_read/write_field 角色/grant/revoke/admin/不在注册表 + field_filter + ctx）
- [x] 4.3 tests/integration/test_custom_permission.py（grant→可见 / revoke→屏蔽 / effective 结构 / 缓存失效）
- [x] 4.4 tests/api/test_permission_api.py（grant/revoke 非 admin 403 + effective 结构 + 未知 scope 422）

### Build & Test
- [ ] B.1 Docker PG16:5551 + Redis7:6406；alembic upgrade head（含 012）；U09 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行 Batch 1；后续"继续"逐批推进至 Build & Test。**
