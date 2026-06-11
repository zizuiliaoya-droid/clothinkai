# U05 NFR 需求计划（NFR Requirements Plan）

> 单元：U05 — 财务结款核心  
> 范围：U05 特异性 NFR；通用 NFR 全部继承 U01 + U02 + U03 + U04

---

## 1. 与 U01-U04 NFR 基线的关系

### 1.1 完全继承
- 全部 U01 通用 NFR（多租户 / 审计 / 备份 / 健康检查 / Token / Sentry / Prometheus / RLS 双引擎 / structlog / pytest 框架）
- U02 模式（partial UNIQUE — **U05 不复用，FB3 用永久 UNIQUE**；字段权限硬编码 — 复用；audit 脱敏 — 复用）
- U03 模式（GIN trgm 单字段 — 复用 settlement_no）
- U04 模式（FB1 状态语义 / FB2 序列号原子 / FB6 防重复注册 + handler flush / FB7 状态机 WHERE 强化 / FB8 日期一致性入口）

### 1.2 U05 增量
- 容量：MVP ≤ 2 万 settlement / 租户（与 U04 promotion 同量级，因 1:1 关系）
- **新增需求**：跨单元事务一致性（U04 review approve + U05 settlement 创建同事务原子性 SLA）
- **新增需求**：attachment 6 项强校验性能（FB4：每次 mark_paid 多 1 次 attachment SELECT + 6 项校验）
- **新增需求**：双口径汇总 SQL 性能（FB7：activity 涉及 audit_log 跨表 join；as_of 简单 GROUP BY）
- **新增需求**：SettlementPaid 反向事件丢失容忍度（FB5：通知类，可丢弃）
- **新增需求**：attachment GC 引用计数（V1，settlement.payment_proof_attachment_id 不能被 GC）
- 字段权限：PAYMENT_VISIBLE_ROLES + PAYMENT_WRITABLE_ROLES + PROOF_UPLOAD_ROLES（3 类，比 U04 多一类）
- **新增需求**：财务记录不可替换审计追溯（FB3：DELETE 路径 405 + 极少手动 SQL 必含 audit）

### 1.3 与 U04 关键差异

| 维度 | U04 | U05 |
|---|---|---|
| 业务键 partial UNIQUE | 使用 `WHERE is_active=true` | **永久 UNIQUE 不带 partial（FB3）** |
| is_active 字段 | 有（软停用） | **无（FB3：财务记录不可软删）** |
| 衍生字段计算 | CTE + Python 双实现（urge_status / dual_platform） | **不需要 CTE**（settlement 字段全部持久化） |
| 状态机数量 | 3 个并行（publish / recall / settlement_request） | 1 个（settlement_status） |
| 事件 | 发出 SettlementRequested（required）+ PromotionPublished（optional） | 监听 SettlementRequested + 发出 SettlementPaid（optional） |
| handler flush | service.review approve 发事件后 commit | **handler 内立即 flush（FB6）** |
| attachment 引用 | 无（U04 不涉及 attachment） | **payment_proof_attachment_id 6 项强校验（FB4）** |
| 删除接口 | DELETE 软停用（is_active=false） | **DELETE 返回 405（FB3）** |

---

## 2. 计划步骤

### Step 1 — 分析功能设计
- [x] 1.1 读取 U05 3 份功能设计文档
- [x] 1.2 与 U01-U04 NFR 基线对齐复用边界

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出 U05 增量 NFR 维度
- [x] 2.2 列出澄清问题（已预填默认值）
- [x] 2.3 等待用户填答 [Answer]

### Step 3 — 生成 nfr-requirements.md
- [x] 3.1 性能 SLA（事件 handler / 状态推进 / 双口径汇总 / attachment 校验）
- [x] 3.2 容量预估（settlement 表 + extra_item + sequence + attachment 引用增长）
- [x] 3.3 字段权限威胁模型（payment_amount / amount 不加密理由）
- [x] 3.4 监控指标（自定义指标 5 个）
- [x] 3.5 跨单元事务一致性 NFR（FB1 强一致策略）
- [x] 3.6 SettlementPaid 反向事件容忍度（FB5）
- [x] 3.7 attachment GC 与引用计数 NFR（FB4）
- [x] 3.8 财务记录不可替换审计追溯（FB3）
- [x] 3.9 测试覆盖（与前面单元一致门槛 + U05 特异性场景）

### Step 4 — 生成 tech-stack-decisions.md
- [x] 4.1 复用 U01-U04 全部技术栈
- [x] 4.2 状态机实现（复用 U04 模式，单状态机）
- [x] 4.3 事件总线（复用 U04 core/events.py，新增 SettlementPaid + 反向 listener 注册框架）
- [x] 4.4 attachment 引用模式（复用 U01 AttachmentService + 6 项校验封装）
- [x] 4.5 双口径汇总实现选型（直接 SQL CTE / Materialized View 评估）

### Step 5 — 提交完成消息

---

## 3. 澄清问题（请填 [Answer]）

> 12 个核心问题，每问预填合理默认值。

### 3.1 容量与性能

**Q1**：单租户 Settlement 表预期上限？峰值 QPS？

[Answer]:
- MVP 上限 ≤ 2 万 settlement / 租户（与 U04 promotion 1:1 关系，量级一致）
- V1 上限 ≤ 10 万
- V2 上限 ≤ 50 万
- 峰值 QPS：
  - list 30 QPS
  - 详情 20 QPS
  - 状态推进（review / fill_payment / mark_paid）总和 5 QPS
  - daily-summary 5 QPS（前端进入页时拉取）
  - extra_item 写 1 QPS

**Q2**：性能 SLA？

[Answer]:
- `GET /api/settlements/`（列表，无 CTE）：P95 ≤ 200ms / P99 ≤ 400ms
- `GET /api/settlements/{id}` 详情（含 attachment 签名 URL 生成）：P95 ≤ 150ms
- `PUT /api/settlements/{id}/review` 状态推进：P95 ≤ 200ms
- `PUT /api/settlements/{id}/payment-amount`：P95 ≤ 200ms
- `PUT /api/settlements/{id}/payment-proof`（含 attachment 6 项校验 + SettlementPaid 事件）：P95 ≤ 300ms
- `POST /api/settlements/{id}/extra-items`（含 total_amount 重算）：P95 ≤ 200ms
- `GET /api/settlements/daily-summary/as-of`（GROUP BY）：P95 ≤ 100ms
- `GET /api/settlements/daily-summary/activity`（含 audit_log JOIN）：P95 ≤ 300ms（FB7）

**Q3**：事件 handler 在 U04 review approve 链路的增量延迟？

[Answer]:
- handler 同事务执行 → INSERT settlement_sequence ON CONFLICT + INSERT settlement + flush
- 单事件增量延迟：P95 ≤ 50ms
- U04 review approve 总 SLA（含 U05 settlement 创建）：P95 ≤ 500ms（与 U04 NFR 一致）
- 不引入异步队列（FB1 强一致优先）

### 3.2 跨单元事务一致性

**Q4**：U04 + U05 同事务的失败一致性策略？

[Answer]: **完全继承 U04 FB1 强一致策略**。

要点：
- U04 review approve + U05 settlement 创建在同一 SQLAlchemy session
- 任一环节失败 → 整个事务回滚（promotion.settlement_status 不变 + settlement 不创建）
- handler 内 flush（FB6）让 UNIQUE / FK 错误立即暴露，不延迟到外层 commit
- handler 失败时 U04 端 `_log_event_dispatch_failure`（脱敏 audit + Sentry capture + 重新 raise）
- audit 用独立 bypass session 写（FB5 防被原事务回滚带走）

部署一致性约束（继承 U04 FB10 多层防护）：
- Migration：U05 必须与 U04 同批部署（007/008 紧跟 006）
- CI：`validate-event-listeners` job 已就绪（U04 batch 4 实施），grep `from app.modules.finance.listeners import register` 必须存在
- Smoke：staging deploy 后跑 `test_review_approve_creates_settlement_via_event` 端到端
- Startup：register_event_listeners 失败 fail fast（U04 batch 4 已实施）

### 3.3 attachment 校验性能（FB4）

**Q5**：mark_paid 时 6 项 attachment 校验的性能？

[Answer]:
- attachment 表已在 U01 实施，单行 SELECT P95 ≤ 5ms（attachment.id 主键索引）
- 6 项校验全部内存判断（无额外 SQL）
- 总开销：单次 mark_paid 多 1 次 SELECT + 6 项判断 → P95 ≤ 10ms 增量
- 远低于 PUT 端点的 300ms SLA，可忽略

**Q6**：attachment 跨租户访问尝试如何监控？

[Answer]:
- service 校验 `attachment.tenant_id == user.tenant_id` 失败时：
  - 抛 `InvalidAttachmentReferenceError(422)`
  - 立即 Sentry capture（warning level + tag = "potential_cross_tenant_attempt"）
  - audit_log `settlement.attachment_cross_tenant_attempt` (resource_id = settlement_id, after = {attachment_id, attempted_tenant_id})
  - 不在响应中暴露 attachment 是否存在 / 属于谁（仅返回通用 422）

监控指标 `attachment_cross_tenant_attempts_total` (Counter, labels: source_module)。

### 3.4 双口径汇总实现（FB7）

**Q7**：daily-summary 实现策略？直接 SQL 还是 Materialized View？

[Answer]: MVP 用直接 SQL（无 Materialized View）：

**as_of 口径**（GROUP BY settlement_status）：
- 走 `idx_settlement_tenant_status`
- 10 万行单租户 P95 ≤ 100ms（GROUP BY 简单查询）
- 不需要 Materialized View

**activity 口径**（含 audit_log JOIN）：
- 走 audit_log.created_at 索引 + settlement.id 主键
- audit_log 1 年保留期内（U01 决策）扫描所有当天 settlement.* action
- 10 万 settlement + 1000 当天 action P95 ≤ 300ms
- V1+ 评估：若日均 audit 行数 > 10 万 / 租户 → 引入 daily_settlement_activity Materialized View（每小时刷新）

V1+ 升级触发条件：
- as_of P95 > 200ms 或 activity P95 > 500ms 持续 5min
- 单租户 settlement > 10 万

### 3.5 attachment GC 与引用计数（FB4）

**Q8**：attachment GC 任务如何避免删除被 settlement 引用的 attachment？

[Answer]:
- U01 attachment 表设计了 `reference_count` 字段（V1 实施时启用）
- U05 在 mark_paid 时调 `AttachmentService.acquire_reference(attachment_id)` 增加引用计数
- attachment GC 任务（V1 Celery beat）扫描 `reference_count == 0 AND created_at < NOW() - INTERVAL '7 days'` 才删除
- MVP 阶段 attachment GC 任务尚未实施（U01 只搭框架），所以 settlement.payment_proof_attachment_id 引用的 attachment 不会被误删
- V1 attachment GC 实施时**必须先**实现引用计数 + 测试 settlement 引用保护

**Q9**：admin 极少手动作废 settlement 时 attachment 如何处理？

[Answer]:
- admin 通过手动 SQL 修改 settlement 数据时（FB3 极少场景）：
  - 不删 attachment 行
  - audit_log 记录变更（必填）
  - 若需释放 attachment（确认无业务价值），admin 单独调 `AttachmentService.release_reference(attachment_id)`
- V2 order_adjustment 调整单流程：保留旧 settlement + attachment 引用，新建 adjustment 行（不重新分配 attachment）

### 3.6 SettlementPaid 反向事件容忍度（FB5）

**Q10**：SettlementPaid 反向事件丢失对系统的影响？

[Answer]: **可容忍**（required_handler=False，通知类）。

影响范围：
- 仅影响 U04 promotion.settlement_status 反向同步
- promotion.settlement_status 保持"待付款"而非"已付款"
- 用户在 U04 端看到的 promotion.settlement_status 与 U05 端 settlement.settlement_status 短暂不一致

恢复机制：
- V1 引入 reconcile Celery beat 任务（每天凌晨 03:00）：
  - 扫描 settlement_status="已付款" 但对应 promotion.settlement_status="待付款" 的 promotion
  - 批量同步推进到"已付款"
  - audit_log 记录 reconcile 数量
- MVP 阶段允许短暂不一致；以 settlement 为 source of truth

监控：
- `settlement_paid_sync_no_match_total` Counter（promotion 端 UPDATE WHERE 0 行的次数）
- 阈值 > 5/min 持续 5min → Sentry warning

### 3.7 字段权限威胁模型

**Q11**：payment_amount / amount / total_amount 字段是否加密存 DB？

[Answer]: 不加密（与 U02 cost_price / U03 quote / U04 quote_amount 决策一致）。

威胁模型：
- 仅防普通业务用户跨角色越权读取（PR / designer / merchandiser 不应看到金额）
- 不防 DBA / 运维（视为可信）
- audit_log 仅记 `*_changed: true` 标记（FB3 + FB4 强化）
  - amount / total_amount / payment_amount → 仅记 `*_changed: true`
  - payment_proof_attachment_id → 仅记 `attachment_id_changed: true`（避免暴露 attachment 内部 ID 与 R2 路径关联）
- audit_log 跨实体访问（如不同 PR 看不同人提交的 settlement）通过 RLS + service 层 `WHERE pr_id=user.id`（PR 角色）

演进选项（V2+）：合规要求时引入 pgcrypto + KMS 集成。

### 3.8 监控指标

**Q12**：U05 需要哪些自定义 Prometheus 指标？

[Answer]: 复用 U01-U04 基线 + 新增 5 个：

1. `settlement_state_transitions_total` (Counter, labels: from_state, to_state)
   - 监控状态机转移频次、非法转移率（与 U04 promotion_state_transitions_total 同模式）

2. `settlement_created_via_event_total` (Counter, labels: result)
   - result ∈ {created, duplicate_skipped, error}
   - 监控事件 handler 创建成功率（FB1 强一致 + 三重幂等）

3. `settlement_sequence_lock_duration_seconds` (Histogram, buckets: 10ms/50ms/100ms/500ms/1s/5s)
   - 监控 settlement_sequence INSERT ON CONFLICT 锁等待时间（与 U04 promotion_sequence 同模式）

4. `attachment_validation_failures_total` (Counter, labels: failure_type)
   - failure_type ∈ {tenant_mismatch, bucket_invalid, purpose_invalid, mime_invalid, size_too_large, status_not_ready}
   - 监控 attachment 6 项强校验失败分布（FB4）
   - 跨租户尝试时同时 Sentry capture

5. `settlement_paid_sync_no_match_total` (Counter)
   - U04 端 listener UPDATE promotion.settlement_status WHERE 0 行的次数
   - 阈值 > 5/min 持续 5min → Sentry warning（FB5 反向同步监控）

实现位置：`backend/app/core/metrics.py`（追加），与 U01-U04 共存。

Sentry tag：新增 `module=finance`。

### 3.9 测试覆盖

**Q13**：U05 测试覆盖关键场景？

[Answer]: 覆盖率门槛与 U04 一致（service ≥ 80% / domain ≥ 90% / api ≥ 60%）。

集成测试必须覆盖（共 ~26 场景）：

**事件处理 / 幂等（FB1+FB3+FB6）**：
1. **EP06-S02 事件创建 settlement**（mock SettlementRequested → 创建成功 + settlement_status="待核查"）
2. **三重幂等 1**：同 promotion_id 重复事件 → 第二次返回 no-op + audit duplicate_skipped
3. **三重幂等 2**：同 request_event_id 重投 → DB UNIQUE 阻止 + service SELECT 兜底
4. **flush 立即暴露错误**（mock IntegrityError → handler 抛错 + U04 事务回滚）
5. **U05 listener 缺失**（在测试中 clear_handlers → U04 dispatch SettlementRequested 抛 MissingRequiredHandlerError → review approve 失败回滚）
6. **handler 抛异常 → 跨单元事务回滚**（mock listener 抛 RuntimeError → promotion.settlement_status 也回滚）

**状态机（FB7 模式）**：
7. EP06-S03 待核查 + approve → 待付款（含状态机 WHERE）
8. EP06-S04 reject + reason → 已驳回
9. **自审禁止**（settlement.pr_id == reviewer.id → 403）
10. EP06-S04 reject 缺 reason → 422
11. **状态推进并发**（100 并发 mark_paid 同 settlement → 1 成功 99 冲突）
12. **跨租户状态推进**（A 租户 settlement，B 租户用户 mark_paid → UPDATE 0 行 → 409）

**extra_item**：
13. EP06-S05 增加 extra_item → total_amount 重算
14. EP06-S05 非 PR 主管 → 403
15. settlement_status != 待付款 → 422

**fill_payment**：
16. EP06-S06 待付款 + payment_amount → 待财务付款

**mark_paid + attachment 强校验（FB4）**：
17. EP06-S07 完整流程：上传 attachment → mark_paid → 已付款
18. EP06-S07 缺字段（payment_date / attachment_id 任一）→ 422
19. EP06-S07 已付款重复上传 → 409
20. **attachment 跨租户**（A 租户 attachment_id 用于 B 租户 settlement → 422 + Sentry warning + audit）
21. **attachment bucket != private** → 422
22. **attachment purpose != settlement_proof** → 422
23. **attachment mime 不在白名单** → 422
24. **attachment status != ready** → 422

**双口径汇总（FB7）**：
25. **daily-summary/activity**（当日 created/approved/paid/rejected 计数 + 金额）
26. **daily-summary/as-of**（截至当日按 status GROUP BY + outstanding_total）

**反向事件（FB5）**：
27. **mark_paid 发 SettlementPaid 事件 → U04 listener 同步 promotion.settlement_status='已付款'**
28. **U04 listener 缺失** → SettlementPaid no-op（不抛错，FB5 通知类）

**财务记录不可替换（FB3）**：
29. **DELETE /api/settlements/{id} → 405**
30. **promotion 软删时 settlement 不级联**

**性能 + 多租户**：
31. 性能基准 `test_settlement_list_perf_with_10k_records`
32. 多租户隔离回归（A 租户用户看不到 B 租户 settlement）
33. settlement_no 9999 溢出 → 500

**端到端**：
34. **EP06 完整 J4**：U04 review approve → U05 settlement 创建 → approve → fill_payment → upload_proof → mark_paid → SettlementPaid → U04 promotion 同步

---

## 4. 决策摘要（用户填答后由 AI 整理）

无明显歧义。所有决策基于：
- INCEPTION U04+U05 同批部署 + FB1 强一致
- U04 已落地的代码契约（events.py / state_machines.py / metrics.py / register_event_listeners 框架）
- 8 P1 反馈守护（FB1-FB8 全部体现）
- 复用 U02/U03/U04 模式（partial UNIQUE 除外，FB3 永久）

---

## 5. 与下一阶段衔接

NFR Requirements 完成后：
- 进入 NFR Design：解决"如何实施"问题（事件总线注册框架扩展 / attachment 校验封装 / 双口径汇总 SQL 模板 / 反向事件 listener 注册）
- 关键设计决策：
  - SettlementPaid 反向 listener 注册位置（modules/promotion/listeners.py vs modules/finance/listeners.py 双向注册）
  - attachment 6 项校验封装（service 层 helper vs AttachmentService 内置）
  - 双口径汇总的 SQL 是放在 repository.py 还是单独 service.py
  - cross-tenant attachment 尝试的 Sentry breadcrumb / audit 时机

---

**等待用户审阅 [Answer]，回复"继续"后进入 Step 3-4 生成 nfr-requirements.md + tech-stack-decisions.md。**
