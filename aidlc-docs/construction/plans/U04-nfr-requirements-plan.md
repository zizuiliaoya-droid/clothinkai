# U04 NFR 需求计划（NFR Requirements Plan）

> 单元：U04 — 推广合作核心  
> 范围：U04 特异性 NFR；通用 NFR 全部继承 U01 + U02 + U03

---

## 1. 与 U01/U02/U03 NFR 基线的关系

### 1.1 完全继承
全部通用 NFR + U02 4 个模式（字段权限 / 审计脱敏 / 原子 upsert（U04 不需要）/ 软删引用检查）+ U03 防侧信道（U04 无敏感联系人字段，不适用）。

### 1.2 U04 增量
- 容量：MVP 2 万 promotion / 租户（V1 10 万 / V2+ 50 万）
- **新增需求**：状态机性能（10 万行下转移操作 P95 ≤ 200ms）
- **新增需求**：列表 CTE 计算性能（urge_status / dual_platform 实时算 P95 ≤ 300ms）
- **新增需求**：本地事件总线 SLA（同事务 dispatch P95 ≤ 50ms）
- **新增需求**：序列号生成并发安全（行级锁竞争测试）
- 字段权限：AMOUNT_VISIBLE_ROLES + AMOUNT_WRITABLE_ROLES（与 U02/U03 模式一致）

---

## 2. 计划步骤

### Step 1 — 分析功能设计
- [x] 1.1 读取 U04 3 份功能设计文档
- [x] 1.2 与 U01/U02/U03 NFR 基线对齐复用边界

### Step 2 — 创建本计划（含澄清问题）
- [x] 2.1 列出 U04 增量 NFR 维度
- [x] 2.2 列出澄清问题（已预填默认值）

### Step 3 — 生成 nfr-requirements.md
- [x] 3.1 性能 SLA（list/CTE/写/状态推进/审核 events）
- [x] 3.2 容量预估
- [x] 3.3 字段权限威胁模型（quote_amount/cost_snapshot 不加密理由）
- [x] 3.4 监控指标（自定义指标 4 个）
- [x] 3.5 测试覆盖（与前面单元一致门槛 + 状态机/事件总线/序列号并发场景）
- [x] 3.6 事件总线 NFR（一致性 / 失败处理 / 升级路径）

### Step 4 — 生成 tech-stack-decisions.md
- [x] 4.1 复用 U01/U02/U03 全部技术栈
- [x] 4.2 状态机实现选型（U01 core/state_machine.py 基类）
- [x] 4.3 本地事件总线实现选型（asyncio + dict registry）
- [x] 4.4 序列号生成方案对比（行级锁 vs PostgreSQL Sequence 对象 vs Redis）
- [x] 4.5 CTE 性能方案对比（CTE vs Materialized View vs 触发器）

### Step 5 — 提交完成消息

---

## 3. 澄清问题（请填 [Answer]）

> U04 比 U02/U03 复杂，10 个核心问题需要确认。

### 3.1 容量与性能

**Q1**：单租户 Promotion 表预期上限？峰值 QPS？

[Answer]: 
- MVP 上限 2 万 promotion / 租户（业务文档基线 5494 × 4 倍冗余）
- V1 上限 10 万
- V2 上限 50 万
- 峰值 QPS：list 50 QPS / 详情 30 QPS / 写 10 QPS / 状态推进 5 QPS / 审核 2 QPS

**Q2**：性能 SLA？

[Answer]:
- `GET /api/promotions/`（含 CTE 计算 urge_status + dual_platform）：P95 ≤ 300ms / P99 ≤ 600ms
- `GET /api/promotions/{id}` 详情：P95 ≤ 100ms
- `POST /api/promotions/`（含序列号锁 + 快照 + 重复检测）：P95 ≤ 300ms / P99 ≤ 800ms
- `PUT /api/promotions/{id}/{publish/cancel}`（状态推进 + 自动推进 settlement + 事件分发）：P95 ≤ 300ms
- `POST /api/promotions/{id}/review`（含 SettlementRequested 事件 + U05 同事务创建 settlement）：P95 ≤ 500ms（含跨单元事务）

### 3.2 状态机性能

**Q3**：3 状态机的转移操作在高并发下是否需要锁？

[Answer]: 不需要全局锁。每个 promotion 的状态字段更新通过 SQLAlchemy 默认乐观并发（updated_at 比对）即可：
- 状态推进 SQL：`UPDATE promotion SET publish_status='已发布', ... WHERE id=:id AND publish_status='未发布' RETURNING id`
- 若 RETURNING 为空 → 其他事务已推进，返回 409 `STATE_TRANSITION_CONFLICT` 让用户重试
- 不引入悲观锁（避免状态推进路径被锁拖慢）

### 3.3 序列号并发

**Q4**：promotion_sequence 行级锁的并发性能？

[Answer]:
- 单租户单天峰值创建率预估：50 promotion/min（PR 集中录入），即 ~1 次/秒
- 行级锁 `SELECT FOR UPDATE` 对单行（tenant_id, date_key）的串行化在 1 QPS 下完全可接受
- P99 创建延迟 ≤ 800ms（含锁等待 + INSERT promotion + flush）
- 高负载场景（V2+ 多租户大量并发）：评估改用 PostgreSQL `Sequence` 对象（每租户每天一个 sequence，需建表登记），但 MVP 不引入

### 3.4 列表 CTE 性能

**Q5**：含 CTE 计算的列表查询性能怎么保证？

[Answer]:
- 主要靠索引：`idx_promotion_publish_dates(tenant_id, publish_status, scheduled_publish_date)` 支撑 urge_status WHERE 筛选
- `idx_promotion_style_publish` 支撑 dual_platform 子查询
- 10 万行 / 租户预估 P95 ≤ 300ms（CTE 实测，因 CTE 在 PostgreSQL 14+ 是 inlined optimization）
- nightly 性能基准测试 `test_promotion_list_perf_with_10k`
- 不达标的诊断顺序：EXPLAIN ANALYZE 验证 / ANALYZE 刷新统计 / 评估添加 stored generated columns（10 万+ 时）

### 3.5 安全（字段权限）

**Q6**：quote_amount / cost_snapshot 字段是否需要 DB 加密？

[Answer]: 不加密（与 U02 cost_price / U03 quote 决策一致）。威胁模型：
- 仅防普通业务用户跨角色越权读取（设计师 / 跟单 / 运营 不应看到金额）
- 不防 DBA / 运维（视为可信）
- audit_log 仅记 `*_changed: true` 标记，不存历史值
- 演进选项（V2+）：合规要求时引入 pgcrypto + KMS 集成

### 3.6 监控指标

**Q7**：U04 需要哪些自定义 Prometheus 指标？

[Answer]: 复用 U01-U03 基线 + 新增 4 个：
1. `promotion_state_transitions_total` (Counter, labels: from_state, to_state, status_field)
   - status_field ∈ {publish, recall, settlement}
   - 监控状态机转移频次、非法转移率
2. `settlement_requested_events_total` (Counter, labels: result)
   - result ∈ {dispatched, handler_failed, no_handler}
   - 监控事件分发成功率、U05 监听器失败率
3. `promotion_sequence_lock_duration_seconds` (Histogram, buckets: 10ms/50ms/100ms/500ms/1s/5s)
   - 监控 `SELECT FOR UPDATE` 锁等待时间，发现并发热点
4. `promotion_search_results_count` (Histogram, buckets: 0/1/10/100/1000)
   - 复用 U02/U03 search 指标命名风格

实现位置：`backend/app/core/metrics.py`（追加），与 U02/U03 共存。

Sentry tag：新增 `module=promotion`。

### 3.7 数据迁移

**Q8**：5494 行历史 promotion 数据如何迁移？

[Answer]: 不在 U04 阶段实施。MVP 启用后由 U06d（推广导入适配器）通过 Excel 模板批量上传。U06d 调用 `PromotionService.create_promotion()` 公共 API（不需 upsert，因 promotion 创建即唯一）。U04 提供 0 数据起步能力。

历史数据迁移特殊性：旧数据可能没有 internal_code 或格式不一致 — U06d 需提供"沿用历史 internal_code"或"重新生成 internal_code"两种策略，由租户配置选择。

### 3.8 事件总线 NFR

**Q9**：本地事件总线的一致性 / 失败处理 / 升级路径？

[Answer]:

**一致性（同事务事件总线）**：
- 监听器与 publisher 共享同一 SQLAlchemy session 和数据库事务
- 监听器抛异常 → 整个事务回滚（U04 review 不成功，promotion.settlement_status 不前进，U05 也不创建 settlement）
- 这是 MVP 阶段最简单可靠的一致性保证（无需 Outbox / Saga）

**失败处理**：
- 监听器异常自然冒泡到 service 层
- service 层 try/except 包装，记录 Sentry 后**重新抛出**（让事务回滚）
- audit_log 记录失败事件 `promotion.review.event_dispatch_failed`

**升级路径**：
- V1+ 视实际需要升级为 Celery / Redis Streams（解耦时机）
  - 触发条件：单 promotion 触发 ≥ 5 个监听器；或监听器涉及外部 API（如 U07 企微）
  - 升级策略：引入 Outbox 模式（同事务写 outbox 表，异步 worker 投递到 Celery）
- 当前 MVP（U04+U05）只有 1 个监听器（U05），同事务足够

### 3.9 测试覆盖

**Q10**：U04 测试覆盖关键场景？

[Answer]: 覆盖率门槛与 U02/U03 一致（service ≥ 80% / domain ≥ 90% / api ≥ 60%）。

集成测试必须覆盖（共 ~25 场景）：
1. EP05-S02 创建 + internal_code 自动生成
2. 序列号防 race（并发 100 次创建，无重复 internal_code）
3. EP05-S04 重复检测 warning（不阻塞）
4. EP05-S06 urge_status 7 个 GWT 场景（5 状态 + 已发布 + 已取消）
5. **urge_status Python vs SQL 一致性**（100 mock 数据双实现）
6. EP05-S07 publish 同事务推进 settlement_status="待核查"
7. EP05-S08 取消已发布 → 422
8. EP05-S09 召回流程（start/success/failure + 跨状态机校验）
9. EP05-S10 平台折算系数 + 历史不重算
10. EP05-S11 爆文阈值实时计算
11. EP05-S12 cpl + 零分母 null
12. EP05-S13 审核 approve → SettlementRequested 事件 mock 监听器接收
13. **EP05-S13 审核 approve 不创建 settlement**（mock SettlementService，断言未调 create）
14. EP05-S13 审核 reject + reason → settlement_status="已驳回"
15. **EP05-S13 自审禁止**（pr_id == reviewer.id → 422）
16. **U05 监听器失败回滚**（mock 抛异常，断言 promotion.settlement_status 也回滚）
17. 字段权限矩阵（4 角色 × quote_amount 读 + 写）
18. update_like_count 区分 crawler/user audit
19. 多租户隔离回归
20. 事件总线 dispatch 多监听器
21. 跨状态机校验（recall 启动前 publish_status 校验）
22. internal_code 9999 溢出 → 409
23. 列表 CTE 计算字段
24. 性能基准 `test_promotion_list_perf_with_10k_records`
25. 端到端：创建 → publish → 审核 → SettlementRequested 事件 + U05 创建 settlement（集成测试）

---

## 4. 决策摘要（用户填答后由 AI 整理）
