# U09 功能设计计划（Functional Design Plan）

> 单元：U09 — 字段级权限 + 自定义权限（V1 第一个单元）
> 覆盖故事：EP01-S05（自定义权限授予/撤销）+ EP01-S06（字段级读/写权限）
> 依赖：U01（RBAC + user_permission_override + merge_permissions）+ U02/U03/U04/U05（含敏感字段 + 4 个 legacy_field_permissions）
> 节奏：Functional Design 阶段 = 本计划 + 3 份功能设计文档（同一轮生成）

---

## 1. 单元上下文

### 1.1 现状（U01-U08 遗留）
- **自定义权限（scope 级）已就绪**：`user_permission_override`（grant/revoke）+ `merge_permissions`（角色 ∪ grant - revoke）+ `list_scopes_for_user`。缺：grant/revoke/effective API 端点。
- **字段级权限是过渡态**：U02/U03/U04/U05 各有一个 `legacy_field_permissions.py`（角色硬编码集合），service 层 to_response 过滤 + 写校验。4 套规则分散，需统一。

### 1.2 U09 目标
1. 统一 4 个 legacy 模块 → 一个 **core 字段权限注册表**（entity.field → 可见/可写角色 + 可被自定义 override）。
2. 字段级**自定义授予/撤销**（扩展 override 到 `field.<entity>.<field>:read|write` scope）。
3. EP01-S05 缺失 API：grant/revoke 自定义权限 + GET effective-permissions。
4. 回归 4 模块：删除 legacy_field_permissions，service 改调 core 统一接口。

### 1.3 现有敏感字段清单（4 模块）
| 实体 | 字段 | 默认可见角色 | 默认可写角色 |
|---|---|---|---|
| sku | cost_price / purchase_price | admin/merchandiser/finance | admin/merchandiser/finance |
| blogger | quote | admin/pr/pr_manager/finance | admin/pr/pr_manager |
| blogger | wechat / phone | admin/pr/pr_manager | admin/pr/pr_manager |
| promotion | quote_amount / cost_snapshot | admin/pr/pr_manager/finance | admin/pr/pr_manager |
| settlement | amount / total_amount / payment_amount | admin/pr_manager/finance | payment_amount: admin/pr_manager |

---

## 2. 澄清问题（已预填 [Answer]）

### Q1 — 字段权限承载方式
- [Answer] **core 注册表 + scope 叠加**：`core/security/field_permissions.py` 定义 `FIELD_PERMISSION_REGISTRY`（entity → field → FieldRule{visible_roles, writable_roles}），作为**默认**（迁移 4 legacy 集合）；自定义 override 用 `user_permission_override` 的 `field.<entity>.<field>:read|write` scope 叠加（grant/revoke）。有效字段权限 = (默认按角色 ∪ field grant) − field revoke。

### Q2 — 字段读屏蔽机制
- [Answer] 保留现有 **service to_response 过滤**模式（成熟、已测），但统一调 `field_filter(entity, data, ctx)`（core）；不可读字段从响应**移除**（非 null，避免泄露存在性）。`build_schema_for_user` 实现为"按需裁剪字段集"工具（供需要 OpenAPI 精确 schema 的端点用，可选）。

### Q3 — 字段写拒绝语义
- [Answer] 写不可写字段（payload 显式含该字段）→ 403 `FIELD_PERMISSION_DENIED`（沿用现有异常，移到 core）；不显式提供则不校验（部分更新友好）。

### Q4 — 自定义权限 API（EP01-S05）
- [Answer] 3 端点：`POST /api/users/{id}/permissions/grant` + `POST /api/users/{id}/permissions/revoke`（body: scope；含字段 scope）+ `GET /api/users/{id}/effective-permissions`（返回 scope 列表 + 字段权限矩阵）。权限点 `auth.permission:grant`（admin）。grant/revoke 后失效该用户权限缓存。

### Q5 — 字段权限是否进缓存
- [Answer] 复用现有 `EffectivePermissions`（scopes frozenset 已含 field scope）；字段判定基于 scopes + 角色默认注册表，无需额外缓存。注册表是代码常量（进程级）。

### Q6 — 回归范围
- [Answer] 删除 4 个 `legacy_field_permissions.py`，product/blogger/promotion/finance 的 service 改调 core `field_filter`/`field_writable`；保持现有行为不变（同一组角色默认），新增自定义 override 能力；现有字段权限测试全部回归通过。

### Q7 — keyword 侧信道（U03 BR-U03-50）
- [Answer] blogger keyword 搜索时 wechat 仅当用户对 `blogger.wechat` 有读权限才参与匹配 —— 统一用 core `can_read_field(entity, field, ctx)` 判定，替换原 has_contact_visibility。

### Q8 — 默认角色 seed
- [Answer] 字段 scope 不在 default_roles seed（避免权限表膨胀）；默认按注册表角色集判定，自定义 override 才落 user_permission_override（permission 表需 seed 这些 field scope 以便 FK）。migration 012 seed field permission scope 定义（仅 permission 表，不绑角色）。

---

## 3. 执行步骤

- [x] 3.1 `U09/functional-design/domain-entities.md`：FieldRule 注册表模型 + 字段 scope 命名（field.<entity>.<field>:read|write）+ 自定义 override 复用 user_permission_override + 有效字段权限算法 + 4 模块字段清单
- [x] 3.2 `U09/functional-design/business-rules.md`：BR-U09-NN（字段读过滤/写拒绝/override 叠加/keyword 侧信道/grant-revoke API/缓存失效/admin 通配）+ 错误码
- [x] 3.3 `U09/functional-design/business-logic-model.md`：UC（grant/revoke/effective-permissions + field_filter 读 + field_writable 写 + 4 模块回归映射）+ 与 U01 merge_permissions 契约
- [x] 3.4 诊断器无警告 + 故事 EP01-S05/S06 100% 覆盖

---

## 4. 故事追溯矩阵

| 故事 | 设计落点 |
|---|---|
| EP01-S05 自定义权限 | grant/revoke/effective API + 复用 override + merge_permissions（含 field scope） |
| EP01-S06 字段级权限 | core field_permissions 注册表 + field_filter/field_writable + 4 模块回归 |

---

**等待用户回复"继续"；本轮直接生成 3 份功能设计文档。**
