# U09 NFR 需求计划（NFR Requirements Plan）

> 单元：U09 — 字段级权限 + 自定义权限
> 范围：U09 特异性 NFR 增量（字段过滤性能 / 权限合并复用 / 回归兼容 / 安全）；通用 NFR 继承 U01-U08
> 节奏：NFR Requirements 阶段 = 本计划 + 2 文档（nfr-requirements.md + tech-stack-decisions.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增依赖
- [Answer] **零新增运行时依赖**：纯 Python dict/set 运算（注册表）+ 复用 U01 权限合并 + Pydantic。

### Q2 — 字段过滤性能
- [Answer] field_filter / can_read_field / can_write_field 是**内存 dict/set 操作**（注册表进程级常量），无额外 DB 查询；单响应过滤 O(字段数)，可忽略。effective-permissions 复用现有权限加载（1 DB + Redis 缓存）。

### Q3 — 权限合并复用
- [Answer] 复用 U01 `merge_permissions`（角色 ∪ grant − revoke）+ `list_scopes_for_user`（已返回含 field scope 的 grants/revokes）+ `EffectivePermissions`；不重写合并逻辑。grant/revoke 后 `invalidate_user_permissions_cache`。

### Q4 — migration 增量
- [Answer] migration 012 **仅 seed 字段 scope 定义**到 permission 表（10 个 `field.<entity>.<field>:read|write` 等），幂等 ON CONFLICT DO NOTHING；**不绑角色**（默认按注册表判定）；无新表。

### Q5 — 回归兼容性保证
- [Answer] 默认角色集与 4 legacy 模块完全一致（值迁移不变）；所有现有字段权限测试（test_*_field_perms / U02/U03/U04/U05 集成）回归全绿；新增 override 测试。删除 4 legacy 模块 + 重复 FieldPermissionDenied。

### Q6 — 安全
- [Answer] 字段读不可见 → 从响应**移除**（非 null，防存在性泄露）；keyword 侧信道：无读权限字段不参与匹配；撤销优先级最高；多租户 override RLS 隔离；grant/revoke 写 audit。

### Q7 — 监控
- [Answer] 不新增自定义 Prometheus 指标；structlog 记 grant/revoke（user_id/scope/effect）；FIELD_PERMISSION_DENIED 由全局 error handler 计入既有 HTTP 4xx 指标。

### Q8 — 测试策略
- [Answer] 单元：can_read/write_field（角色默认 / grant / revoke / 通配 admin / 不在注册表）+ field_filter 移除 + FieldPermissionContext 构造。集成：grant→读可见 / revoke→读屏蔽 / effective-permissions / 4 模块回归（cost_price 设计师不可见 / quote finance 只读 / payment_amount / keyword wechat 侧信道）。API：grant/revoke 鉴权 + effective 结构。

---

## 2. 执行步骤

- [x] 2.1 `U09/nfr-requirements/nfr-requirements.md`：性能（内存过滤）+ 权限合并复用 + 回归兼容 + 安全（移除/侧信道/撤销优先）+ migration 012 seed + 测试 + 故事映射 + 一致性校验
- [x] 2.2 `U09/nfr-requirements/tech-stack-decisions.md`：零新增依赖 + FieldRule/注册表结构 + can_read/write_field 实现 + field_filter + 复用 merge_permissions + migration 012 seed 片段 + 4 模块回归落点
- [x] 2.3 诊断器无警告 + 与 functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 需求文档。**
