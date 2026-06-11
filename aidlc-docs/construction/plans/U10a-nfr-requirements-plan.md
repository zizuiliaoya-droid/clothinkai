# U10a NFR 需求计划（NFR Requirements Plan）

> 单元：U10a — 设计制版全流程
> 范围：U10a 特异 NFR 增量（状态机一致性 / 通知同事务 / 自动核价 / 文件存储 / 多租户）；通用 NFR 继承 U01-U09
> 节奏：NFR Requirements 阶段 = 本计划 + 2 文档（nfr-requirements.md + tech-stack-decisions.md），同一轮生成

---

## 1. 澄清问题（已预填 [Answer]）

### Q1 — 新增依赖
- [Answer] **零新增运行时依赖**：状态机复用 core/state_machine；文件复用 core/attachment（R2 public/private）；通知复用 U07；JSONB 子表用 SQLAlchemy 既有。

### Q2 — 状态机 / 事务一致性
- [Answer] 每个状态推进 = 单事务（子表写入 + design_status 更新 + design_workflow_log + notification 一起 commit/rollback）；状态推进用乐观并发（UPDATE ... WHERE design_status=:from RETURNING，并发冲突 → 409/重试提示），与 U04/U05 一致。

### Q3 — 通知性能
- [Answer] notify 按角色解析租户内 user_ids（1 次查询，索引 user_role(tenant,role)）→ 批量插 notification（N 行，N=该角色人数，通常 <50）；同事务；无外部调用，P95 ≤ 200ms。

### Q4 — 自动核价
- [Answer] submit_costing 写 style 下所有 active SKU.cost_price：1 次 UPDATE（WHERE style_id + is_active）；Decimal 精度 DECIMAL(10,2)；系统口径绕过 U09 字段写校验但写 audit（脱敏）。

### Q5 — 文件存储
- [Answer] 设计稿 → R2 public 桶（复用 U02 main_image_key 路径规约）；版型文件 pattern_file → R2 private 桶（签名 URL 读，复用 U05 attachment 签名 URL 模式）；attachment 校验复用既有。

### Q6 — 多租户 / RLS
- [Answer] 3 子表 + design_workflow_log 均 TenantScopedModel + RLS；查询/聚合显式 tenant 过滤（防御 + 测试 bypass 一致，同既有）；style/sku 跨租户由 RLS + ORM 钩子。

### Q7 — 性能 SLA
- [Answer] 状态推进写 P95 ≤ 300ms；list 分组计数 P95 ≤ 300ms（复用 style 索引 + design_status）；detail 聚合（style+3 子表+log）P95 ≤ 300ms。

### Q8 — 权限 / 安全
- [Answer] 每动作 require_permission(design.*)；admin 通配；reject/cancel/核价写 audit；driven_by 防伪（服务端按当前状态+actor 角色推断，不信任客户端）。

### Q9 — 监控
- [Answer] 不新增自定义 Prometheus 指标；structlog 记状态转移（style_id/from/to/action/actor）；非法转移计入既有 HTTP 4xx。

### Q10 — 测试策略
- [Answer] 单元：DesignStateMachine 全合法转移 + 非法转移 422 + 驳回回退映射 + 自动核价求和 + available_actions。集成：端到端 J1（设计→大货）+ 各环节角色权限 + reject 回退 + cancel 不可逆 + 通知写入 + 自动核价写 SKU + 多租户隔离。API：端点鉴权 + OpenAPI。

---

## 2. 执行步骤

- [x] 2.1 `U10a/nfr-requirements/nfr-requirements.md`：状态机事务一致性 + 乐观并发 + 通知同事务 + 自动核价 + 文件 R2 + 多租户 RLS + 性能 SLA + 安全(driven_by 防伪) + 测试 + 故事映射 + 一致性校验
- [x] 2.2 `U10a/nfr-requirements/tech-stack-decisions.md`：零新增依赖 + core/state_machine 复用 + R2 public/private 落点 + U07 Notification 复用 + RoleRepository.list_user_ids_by_role_code + migration 013 + 4 子表落点 + 测试落点
- [x] 2.3 诊断器无警告（nfr-requirements.md spec-format 假阳性 IGNORE）+ 与 functional-design 一致

---

**等待用户"继续"；本轮直接生成 2 份 NFR 需求文档。**
