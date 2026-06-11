# U10a 非功能需求（NFR Requirements）

> 单元：U10a — 设计制版全流程
> 范围：U10a 特异 NFR 增量；通用 NFR 继承 U01-U09

---

## 1. 与基线的关系

### 1.1 完全继承
- 四层架构 / RLS 多租户 / audit / 全局 error handler / structlog / Prometheus / pytest（U01）
- core/state_machine（U04/U05）/ core/attachment R2 公私桶（U02/U05）/ NotificationService（U07）

### 1.2 U10a 增量
- DesignStateMachine（7 态 + 驳回回退 + 取消终态）
- 3 业务子表 + 1 历史表（migration 013）
- 自动核价写 SKU + 吊牌价
- 按角色通知（RoleRepository.list_user_ids_by_role_code）
- design.* 细分 scope seed

### 1.3 不涉及
- 无新依赖 / 无 Celery / 无外部调用 / 无新自定义指标。

---

## 2. 一致性 / 事务 NFR

| 路径 | 要求 |
|---|---|
| 状态推进 | 单事务：子表写 + design_status UPDATE + workflow_log + notification 一起 commit/rollback |
| 并发推进 | 乐观并发 `UPDATE ... WHERE design_status=:from RETURNING`；冲突 → StateTransitionConflict 409 |
| 通知同事务 | notification 与状态变更同事务（失败一起回滚，保证"状态变了必有通知"） |
| 自动核价 | submit_costing 的 SKU cost_price 更新与状态推进同事务 |

---

## 3. 性能 NFR

| 路径 | 指标 | 目标 |
|---|---|---|
| 状态推进写 | P95 | ≤ 300ms（子表 upsert + 1 UPDATE + N notification 插入） |
| list 分组计数 | P95 | ≤ 300ms（复用 style(tenant,design_status) 索引） |
| detail 聚合 | P95 | ≤ 300ms（style + 3 子表 1:1 + log 时间线） |
| 自动核价 UPDATE | — | 1 条 UPDATE WHERE style_id+is_active |
| 通知解析 | — | 1 次 user_role 查询（索引 (tenant,role_id)），批量插 N(<50) 行 |

---

## 4. 安全 NFR

| 威胁 | 防护 |
|---|---|
| 越权操作环节 | 每动作 require_permission(design.*)；admin 通配 |
| driven_by 伪造 | 服务端按当前状态 + actor 角色推断 driven_by，不信任客户端入参 |
| 越权读金额 | 自动核价写 cost_price 后，读仍受 U09 字段权限（sku 响应经 SkuService 过滤） |
| 跨租户访问 style/子表 | RLS + ORM 钩子 + 显式 tenant 过滤 |
| 版型文件越权 | R2 private 桶 + 签名 URL（短期，复用 U05 模式） |
| 审计 | reject/cancel/自动核价 写 audit_log（核价敏感值脱敏） |

---

## 5. 可观测性 NFR

- 不新增自定义 Prometheus 指标。
- structlog 记状态转移（style_id / from / to / action / actor_id / driven_by）。
- 非法转移 ILLEGAL_STATE_TRANSITION / 权限 PERMISSION_DENIED 计入既有 HTTP 4xx。
- design_workflow_log 作为业务可见流程时间线（前端展示，非安全审计）。

---

## 6. migration NFR

- migration 013：创建 style_fabric / style_pattern / style_craft / design_workflow_log（4 表 + RLS + 1:1 UNIQUE(style_id) + FK）+ seed 细分 design.* scope（绑角色，幂等 ON CONFLICT DO NOTHING）。接 012 head。

---

## 7. 多租户 NFR

- 4 新表均 TenantScopedModel + RLS 启用。
- 测试引擎 bypass（RLS OFF）→ 列表/聚合 + 通知目标解析显式 `WHERE tenant_id` 过滤（防御 + 确定性，同既有约定）。

---

## 8. 测试 NFR

| 类型 | 覆盖 |
|---|---|
| 单元 | DesignStateMachine 全合法转移 + 非法 422 + 驳回回退映射 + 自动核价求和 + available_actions 矩阵 |
| 集成 | J1 端到端（设计→大货）+ 各环节角色权限 403 + reject 回退 + cancel 不可逆 + 通知写入（角色解析）+ 自动核价写所有 active SKU + 多租户隔离 |
| API | 端点鉴权 401/403 + OpenAPI 暴露 |
| 覆盖率 | service ≥ 80% / domain（状态机）≥ 90%（继承基线） |

---

## 9. 故事 NFR 映射

| 故事 | NFR 验收 |
|---|---|
| EP03-S02~S11（流程推进） | 单事务一致 + 乐观并发 + 性能 ≤300ms |
| EP03-S06/S12（驳回） | 回退映射正确 + 通知上游 + audit |
| EP03-S13（取消） | 不可逆 + admin only + audit |
| EP03-S14（通知） | 同事务 + 按角色解析 + unread-count 复用 U07 |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ §1.3 |
| 状态推进单事务 + 乐观并发 | ✅ §2 |
| 通知同事务 + 角色解析 | ✅ §2/§3 |
| 自动核价同事务写 SKU | ✅ §2/§3 |
| driven_by 服务端推断防伪 | ✅ §4 |
| migration 013 4 表 + scope seed | ✅ §6 |
| 4 表 RLS + 显式 tenant 过滤 | ✅ §7 |

> 注：nfr-requirements.md 触发 spec-format 假阳性（Missing ## Introduction/## Requirements）= 已知，IGNORE。
