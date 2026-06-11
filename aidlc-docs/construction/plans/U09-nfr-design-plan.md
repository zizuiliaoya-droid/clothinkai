# U09 NFR 设计计划（NFR Design Plan）

> 单元：U09 — 字段级权限 + 自定义权限
> 范围：将 NFR Requirements 落地为可实现的设计模式 + 组件清单
> 节奏：NFR Design 阶段 = 本计划 + 2 文档（nfr-design-patterns.md + logical-components.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — ctx 构造来源
- [Answer] `FieldPermissionContext` 由 service 入口构造：`role_codes` ← `RoleRepository.list_codes_for_user(user_id)`（角色 code，如 admin/merchandiser）；`grants/revokes` ← `PermissionRepository.list_scopes_for_user` 返回的 grants/revokes 中**仅 `field.` 前缀子集**；`is_superuser` ← `"*" in merged_scopes`（EffectivePermissions 已算）。

### Q2 — 字段权限上下文获取入口
- [Answer] 新增 deps `get_field_perm_context`（依赖 CurrentActiveUser + session），返回 `FieldPermissionContext`，带 Redis 缓存（复用 perm cache key 派生 `fieldctx:user:<id>`，TTL 同 PERM_CACHE_TTL）；4 模块 service 方法签名追加 ctx 参数（由 api 层注入）。

### Q3 — 模式数量
- [Answer] 2 个增量模式：**P-U09-01**（字段权限注册表 + can_read/write_field + field_filter + ctx 构造）；**P-U09-02**（grant/revoke/effective-permissions API + 复用 merge_permissions + 缓存失效 + audit）。

### Q4 — FieldPermissionDenied 落点
- [Answer] 移至 `core/exceptions.py`（`AppException` 子类，code=`FIELD_PERMISSION_DENIED`，http=403）；删除 4 模块各自重复定义；全局 error handler 已统一序列化。

### Q5 — field_filter 应用位置
- [Answer] 在 service 的 `to_response`/列表组装处对 dict 调用 `field_filter(entity, data, ctx)` 移除不可读字段；写校验在 service 写入前 `can_write_field` 检查 payload 显式字段 → 否则 403。

### Q6 — keyword 侧信道（blogger.wechat）
- [Answer] blogger 搜索：仅当 `can_read_field("blogger","wechat",ctx)` 为真时 wechat 才纳入 ILIKE 匹配条件（BR-U09-50）；否则该字段不参与 WHERE，避免通过命中泄露值。

### Q7 — proof_upload 处理
- [Answer] 不入字段注册表；改为动作 scope `finance.settlement:pay`（已 seed admin/finance），用 `require_permission("finance.settlement","pay")` 守卫。

### Q8 — migration 012 范围
- [Answer] 仅 seed 字段 scope 定义（10 字段 × read/write，settlement.amount/total_amount 只读不含 write scope）到 permission 表；ON CONFLICT (scope) DO NOTHING；不写 role_permission；downgrade 删这些 scope。

---

## 2. 执行步骤

- [x] 2.1 `U09/nfr-design/nfr-design-patterns.md`：P-U09-01（注册表/FieldRule/FieldPermissionContext/can_read_field/can_write_field/field_filter/ctx 构造完整伪代码 + 撤销>授予>角色 + admin 通配 + keyword 侧信道）+ P-U09-02（PermissionService.grant/revoke/get_effective + 复用 merge_permissions + invalidate cache + audit + 3 API 端点完整伪代码 + 鉴权）
- [x] 2.2 `U09/nfr-design/logical-components.md`：新建/改动/删除组件清单（core/security/field_permissions.py 新建；core/exceptions.py +FieldPermissionDenied；auth PermissionService+api 3 端点+deps get_field_perm_context；product/blogger/promotion/finance service 重构；删 4 legacy + 重复异常；migration 012）+ 依赖图 + 测试文件 + 一致性校验
- [x] 2.3 诊断器无警告 + 与 nfr-requirements / functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 设计文档。**
