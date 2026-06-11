# U09 非功能需求（NFR Requirements）

> 单元：U09 — 字段级权限 + 自定义权限
> 范围：U09 特异性 NFR 增量（字段过滤性能 / 权限合并复用 / 回归兼容 / 安全）；通用 NFR 继承 U01-U08

---

## 1. 与基线的关系

### 1.1 完全继承
- RBAC + user_permission_override + merge_permissions + EffectivePermissions + Redis 权限缓存（U01）
- 多租户 RLS / audit / 全局 error handler / pytest 框架（U01）

### 1.2 U09 增量
- **统一字段权限注册表**（core，迁移 4 legacy）
- **字段 scope 自定义 override**（复用 override 表）
- **3 个自定义权限 API**（grant/revoke/effective）
- **4 模块回归**（删 legacy，行为兼容）
- migration 012（仅 permission seed）

### 1.3 不涉及
- 无新表 / 无新依赖 / 无 Celery / 无外部调用 / 无新自定义指标。

---

## 2. 性能 NFR

| 路径 | 指标 | 目标 | 备注 |
|---|---|---|---|
| field_filter（单响应） | — | 内存 O(字段数) | dict/set 运算，无 DB |
| can_read/write_field | — | O(1) | 注册表常量 + set 交集 |
| grant / revoke | P95 | ≤ 150ms | 1 upsert + 缓存失效 |
| effective-permissions | P95 | ≤ 200ms | 复用权限加载（1 DB + Redis 缓存） |

- 注册表是进程级常量（import 时构建），零运行时构建开销。
- 字段过滤不引入额外 DB 查询（与现有 to_response 同一遍序列化）。

---

## 3. 可靠性 / 回归兼容 NFR

- **行为兼容**：FIELD_PERMISSION_REGISTRY 默认角色集与 4 legacy 模块**完全一致**（值迁移不变）；现有字段权限测试（U02/U03/U04/U05）回归全绿。
- **撤销优先级**：撤销 > 授予 > 角色默认（复用 merge_permissions 语义）。
- **缓存一致**：grant/revoke 后 `invalidate_user_permissions_cache(user_id)`，下次请求重新加载。
- **删除清理**：4 个 legacy_field_permissions.py + 各模块重复 FieldPermissionDenied 删除，统一到 core。

---

## 4. 安全 NFR

| 威胁 | 防护 |
|---|---|
| 字段值越权读 | field_filter 从响应**移除**字段（非 null，防存在性泄露） |
| keyword 侧信道（wechat） | 无读权限字段不参与 ILIKE 匹配（BR-U09-50） |
| 越权写敏感字段 | can_write_field → payload 显式含不可写字段 → 403 FIELD_PERMISSION_DENIED |
| 越权授予权限 | grant/revoke 需 auth.permission:grant（admin）；写 audit |
| 跨租户 override | user_permission_override 含 tenant_id（RLS 隔离） |
| 撤销被授予绕过 | 撤销优先级最高 |

- proof_upload 改为动作 scope `finance.settlement:pay`（已 seed admin/finance），非字段权限。

---

## 5. 可观测性 NFR

- 不新增自定义 Prometheus 指标。
- structlog 记 permission.grant / permission.revoke（user_id / scope / effect / actor）。
- FIELD_PERMISSION_DENIED / PERMISSION_DENIED 计入既有 HTTP 4xx 指标（instrumentator）。
- audit_log：grant/revoke 必记录（actor + target user + scope + effect）。

---

## 6. migration NFR

- migration 012：**仅 seed 字段 scope 定义**到 permission 表（10 个 field scope read/write + 必要动作 scope），`ON CONFLICT (scope) DO NOTHING` 幂等；**不绑角色**（role_permission 不动）；无新表 / 无 DDL 变更。
- 接 011 head。

---

## 7. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | can_read_field / can_write_field（角色默认 / grant / revoke / admin 通配 / 不在注册表）+ field_filter 移除 + FieldPermissionContext 构造 |
| 集成 | grant→字段可见 / revoke→字段屏蔽 / effective-permissions 结构 / **4 模块回归**（sku.cost_price 设计师不可见 / blogger.quote finance 只读 + wechat 侧信道 / promotion.quote_amount / settlement.payment_amount 写 403） |
| API | grant/revoke 鉴权（非 admin 403）+ effective-permissions 响应结构 + 未知 scope 422 |
| 覆盖率 | core/security ≥ 85% / service ≥ 80%（继承基线） |

---

## 8. 故事 NFR 映射

| 故事 | NFR 验收 |
|---|---|
| EP01-S05 自定义权限 | grant/revoke ≤ 150ms + 缓存失效 + effective-permissions + audit |
| EP01-S06 字段级权限 | 内存过滤无 DB + 移除非 null + 写 403 + 4 模块回归兼容 |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 / 表 | ✅ §1.3 |
| 字段过滤内存 O(字段数) | ✅ §2 |
| 复用 merge_permissions + 缓存失效 | ✅ §3 |
| 行为兼容（默认角色集不变）+ 回归全绿 | ✅ §3 / §7 |
| 移除非 null + 侧信道 + 撤销优先 | ✅ §4 |
| migration 012 仅 permission seed 幂等 | ✅ §6 |
