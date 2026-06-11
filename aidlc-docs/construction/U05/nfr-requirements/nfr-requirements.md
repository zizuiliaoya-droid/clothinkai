# U05 非功能需求（NFR Requirements）

> 单元：U05 — 财务结款核心  
> 范围：U05 特异性 NFR 增量；通用 NFR 全部继承 U01 + U02 + U03 + U04

---

## 1. 与 U01-U04 NFR 基线的关系

### 1.1 完全继承
- 错误码体系 / 认证 / 授权 / 多租户（U01）
- 字段权限硬编码模式（U02 P-U02-02，适配 PAYMENT_VISIBLE_ROLES + PAYMENT_WRITABLE_ROLES + PROOF_UPLOAD_ROLES — 比 U04 多一类）
- 审计敏感值脱敏（U02/U03/U04 同模式）
- 监控（Prometheus + Sentry + Loki）
- U04 模式（FB1 状态语义 / FB2 序列号原子 / FB6 防重复注册 + handler flush / FB7 状态机 WHERE 强化 / FB8 日期一致性入口）

### 1.2 U05 增量
- 容量：MVP ≤ 2 万 settlement / 租户（与 U04 promotion 1:1 关系，量级一致）
- **新增**：跨单元事务一致性（U04+U05 同事务原子，FB1）
- **新增**：attachment 6 项强校验性能 + 跨租户监控（FB4）
- **新增**：双口径汇总 SQL 性能（FB7：as_of GROUP BY 简单；activity 跨 audit_log JOIN）
- **新增**：SettlementPaid 反向事件丢失容忍度（FB5：通知类，可丢弃）
- **新增**：attachment GC 引用计数 NFR（FB4：V1 settlement 引用保护）
- **新增**：财务记录不可替换审计追溯（FB3：DELETE 405 + 极少手动 SQL 必含 audit）
- **新增**：5 个自定义 Prometheus 指标

### 1.3 与 U04 关键差异

| 维度 | U04 | U05 |
|---|---|---|
| 业务键 partial UNIQUE | partial（WHERE is_active=true） | **永久（FB3）** |
| is_active 字段 | 有 | **无（FB3）** |
| 衍生字段 CTE | 双实现（urge / dual_platform） | 不需要（无衍生字段） |
| 状态机数 | 3 并行 | 1 |
| handler flush | service 层 commit | **handler 内立即 flush（FB6）** |
| attachment | 无 | **6 项强校验（FB4）** |
| DELETE 接口 | 软停用 | **405（FB3）** |

---

## 2. 容量需求

### 2.1 数据规模（单租户）

| 表 | MVP 上限 | V1 上限 | V2+ 上限 |
|---|---|---|---|
| `settlement` | 20,000 行 | 100,000 行 | 500,000 行 |
| `settlement_extra_item` | 5,000 行 | 30,000 行 | 150,000 行 |
| `settlement_sequence` | 365 行/年 | 365 行/年 | 365 行/年 |

settlement 与 promotion 1:1 关系，与 U04 容量基线对齐。

### 2.2 并发负载

| API | 平均 QPS | 峰值 QPS | 触发场景 |
|---|---|---|---|
| `GET /api/settlements/` 列表 | 5 | 30 | PR 主管 / 财务浏览页面 |
| `GET /api/settlements/{id}` 详情（含 attachment 签名 URL） | 3 | 20 | 详情页打开 |
| `PUT /api/settlements/{id}/review` | 0.5 | 5 | PR 主管批量核查 |
| `POST /api/settlements/{id}/extra-items` | 0.2 | 2 | 增加运费 / 赞奖 |
| `PUT /api/settlements/{id}/payment-amount` | 0.5 | 3 | PR 主管确认金额 |
| `PUT /api/settlements/{id}/payment-proof` | 0.5 | 3 | 财务上传截图 |
| `GET /api/settlements/daily-summary/as-of` | 1 | 5 | 进入页时拉取（默认调） |
| `GET /api/settlements/daily-summary/activity` | 0.5 | 3 | 月底对账查询 |

### 2.3 增长触发器
- 单租户 settlement 突破 10 万行 → P95 监控连续 1 周 > 300ms 触发：
  1. 检查 daily-summary 索引使用率
  2. 评估引入 daily_settlement_activity Materialized View（每小时刷新）
  3. 评估按 created_at 分区（PostgreSQL Declarative Partitioning）
- 突破 50 万行 → V1+ 评估读写分离 + audit_log 归档加速

---

## 3. 性能需求

### 3.1 SLA 总表

| API | P50 | P95 | P99 | 超时 |
|---|---|---|---|---|
| `GET /api/settlements/` 列表 | ≤ 60ms | ≤ 200ms | ≤ 400ms | 5s |
| `GET /api/settlements/{id}` 详情（含 attachment 签名 URL） | ≤ 50ms | ≤ 150ms | ≤ 300ms | 3s |
| `PUT /api/settlements/{id}/review` 状态推进 | ≤ 80ms | ≤ 200ms | ≤ 400ms | 5s |
| `POST /api/settlements/{id}/extra-items`（含 total 重算） | ≤ 80ms | ≤ 200ms | ≤ 400ms | 5s |
| `PUT /api/settlements/{id}/payment-amount` | ≤ 80ms | ≤ 200ms | ≤ 400ms | 5s |
| **`PUT /api/settlements/{id}/payment-proof`（含 attachment 6 项校验 + SettlementPaid 事件）** | ≤ 100ms | **≤ 300ms** | ≤ 600ms | 5s |
| `GET /api/settlements/daily-summary/as-of`（GROUP BY） | ≤ 30ms | ≤ 100ms | ≤ 200ms | 3s |
| **`GET /api/settlements/daily-summary/activity`（含 audit_log JOIN）** | ≤ 100ms | **≤ 300ms** | ≤ 600ms | 5s |
| 事件 handler 增量延迟（U04 review approve 链路） | ≤ 20ms | **≤ 50ms** | ≤ 100ms | — |

> mark_paid P95 ≤ 300ms 含 attachment 6 项校验（≤ 10ms）+ UPDATE WHERE 旧状态（FB7）+ SettlementPaid 反向事件 dispatch（通知类，无外部 IO）。
> activity 汇总 P95 ≤ 300ms 因含跨 audit_log + settlement JOIN（FB7）；可通过索引覆盖优化。
> 事件 handler 增量延迟与 U04 review approve 总 SLA 累加：U04 总 P95 ≤ 500ms（含 U05 settlement 创建）。

### 3.2 SLA 适用条件
- 测试基准：10,000 settlement + 各角色组合
- 单租户独立测试
- attachment 表已有 1000+ 行（模拟生产规模）

### 3.3 监控数据源
- **Prometheus** = SLA 真实数据源（与 U02/U03/U04 一致）
- **Sentry** = 异常 + 慢事务抽样 + 跨租户 attachment 尝试 warning

### 3.4 索引必建项

```sql
-- 业务键 + 永久幂等键（FB3）
CREATE UNIQUE INDEX uq_settlement_no ON settlement (tenant_id, settlement_no);
CREATE UNIQUE INDEX uq_settlement_promotion ON settlement (tenant_id, promotion_id);  -- 永久，无 partial
CREATE UNIQUE INDEX uq_settlement_request_event_id ON settlement (request_event_id);
CREATE UNIQUE INDEX uq_settlement_sequence ON settlement_sequence (tenant_id, date_key);

-- 列表筛选 + as_of 汇总
CREATE INDEX idx_settlement_tenant_status ON settlement (tenant_id, settlement_status, created_at DESC);
CREATE INDEX idx_settlement_blogger ON settlement (tenant_id, blogger_id);
CREATE INDEX idx_settlement_style ON settlement (tenant_id, style_id);
CREATE INDEX idx_settlement_pr ON settlement (tenant_id, pr_id);
CREATE INDEX idx_settlement_payment_date ON settlement (tenant_id, payment_date);  -- activity 汇总
CREATE INDEX idx_settlement_reviewed_by ON settlement (tenant_id, reviewed_by);
CREATE INDEX idx_settlement_paid_by ON settlement (tenant_id, paid_by);

-- GIN trgm（关键字搜索；无 partial：所有 settlement 都活跃）
CREATE INDEX idx_settlement_no_trgm ON settlement USING gin (settlement_no gin_trgm_ops);

-- extra_item
CREATE INDEX idx_extra_item_settlement ON settlement_extra_item (tenant_id, settlement_id);
```

> 共 12 索引，与 U04 promotion 风格一致；FB3 移除 `WHERE is_active=true` partial 条件。

---

## 4. 安全（字段权限威胁模型）

### 4.1 不加密决策（与 U02/U03/U04 一致）

字段：`amount` / `total_amount` / `payment_amount` / `extra_item.amount` 不在 DB 层加密。

威胁模型：
- **本决策仅防御**：普通业务用户跨角色越权读取（PR / designer / merchandiser / 跟单 不应看到金额）
- **本决策不防御**：DBA / 运维（视为可信内部人员）
- **应用层防护**：service 层 BR-U05-50/51 + Pydantic schema 字段过滤
- **审计**：所有金额变更进 audit_log 但**仅记 `*_changed: true` 标记**
- **演进选项**：V2+ 合规要求时引入 pgcrypto + KMS 集成

### 4.2 attachment 跨租户访问防御（FB4）

威胁场景：用户提交他人租户的 attachment_id 试图绑定到自己 settlement。

防御机制（详见 BR-U05-60）：
- service 层校验 `attachment.tenant_id == user.tenant_id` 失败时：
  1. 抛 `InvalidAttachmentReferenceError(422)` — 不返回 404 / 不暴露 attachment 是否存在
  2. **立即 Sentry capture**（warning level + tag = "potential_cross_tenant_attempt"）
  3. **audit_log 记录**：`settlement.attachment_cross_tenant_attempt`（after = {attempted_attachment_id, attempted_tenant_id, user_id}）
  4. 监控指标 `attachment_validation_failures_total{failure_type="tenant_mismatch"}` Counter
- RLS 兜底：attachment 表 RLS 策略也会阻止跨租户 SELECT（双层防护）

### 4.3 财务记录不可替换审计追溯（FB3）

要求：
- DELETE /api/settlements/{id} 直接 405（router 层兜底）
- promotion 软删时不级联（独立财务实体留痕）
- 极少场景 admin 手动 SQL 修正：
  - 必须先 SELECT 留意 settlement 当前状态（写入"操作前快照"audit）
  - UPDATE / DELETE 操作必须写 audit_log（actor_type=system + 完整 before/after）
  - Sentry capture warning level（运维通报）
- 任何形式的"软删 + 重建"都被拒绝（永久 UNIQUE 已阻止）

---

## 5. 字段权限矩阵（FB4 + 复用 U02/U03/U04 模式）

```python
# modules/finance/legacy_field_permissions.py
PAYMENT_VISIBLE_ROLES: frozenset[str] = frozenset({
    "admin", "pr_manager", "finance"
})
"""可见 amount / total_amount / payment_amount / extra_item.amount / payment_proof_signed_url 的角色."""

PAYMENT_WRITABLE_ROLES: frozenset[str] = frozenset({
    "admin", "pr_manager"  # finance 仅读不写 payment_amount
})
"""可写 payment_amount 的角色."""

PROOF_UPLOAD_ROLES: frozenset[str] = frozenset({
    "admin", "finance"  # PR 主管不能上传付款截图
})
"""可上传付款截图的角色."""
```

### 5.1 完整权限矩阵

| 角色 | settlement read | review approve/reject | fill_payment_amount | upload_proof | view_payment | add_extra_item |
|---|---|---|---|---|---|---|
| admin | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| pr_manager | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| finance | ✅（限 payment 视图） | ❌ | ❌ | ✅ | ✅ | ❌ |
| pr | ✅（限 promotion.pr_id=自己） | ❌ | ❌ | ❌ | ❌ | ❌ |
| 其他 | ❌ | — | — | — | — | — |

### 5.2 PR 角色 list endpoint 自动过滤

`GET /api/settlements/` 在 PR 角色下自动加 `WHERE pr_id = current_user.id`（service 层注入），不可见非自己提交的 settlement。

### 5.3 演进路线

V1 实施 U09 字段级权限：
- 替换 `legacy_field_permissions.py` 为 `Permission.field_filter()` / `Permission.field_writable()`
- audit_log 仍仅记 `*_changed: true`（FB3 + FB4 强化的脱敏策略保留）


---

## 6. 跨单元事务一致性（FB1 + 继承 U04 决策）

### 6.1 强一致策略
完全继承 U04 FB1 决策：
- **同事务事件总线**：监听器与 publisher 共享同一 SQLAlchemy session 和数据库事务
- 监听器抛异常 → 整个事务回滚（U04 review approve 不成功 + U05 settlement 不创建 + promotion.settlement_status 不前进）
- handler 内立即 `await session.flush()`（FB6）让 UNIQUE / FK 错误立即暴露
- 失败时 U04 端 `_log_event_dispatch_failure`（脱敏 audit + Sentry capture + 重新 raise）
- audit 用独立 bypass session 写（防被原事务回滚带走）

### 6.2 三重幂等（FB1 + FB3）
- DB UNIQUE(tenant_id, promotion_id) — **永久不可替换**（FB3）
- DB UNIQUE(request_event_id) — 永久（事件重放兜底）
- service 层 SELECT 检查 — 友好错误 + audit `settlement.create_skipped_duplicate`

### 6.3 部署一致性约束（继承 U04 FB10 多层防护）

| 层 | 防护 | 状态 |
|---|---|---|
| Migration | U05 必须与 U04 同批部署（007/008 紧跟 006） | ✅ U06 待补 backfill 008 |
| CI | `validate-event-listeners` job 检查 `from app.modules.finance.listeners import register` | ✅ U04 batch 4 已实施 |
| Smoke | staging deploy 后跑 `test_review_approve_creates_settlement_via_event` 端到端 | 🟡 U05 实施时启用 |
| Startup | register_event_listeners 失败 fail fast | ✅ U04 batch 4 已实施 |
| 文档 | 部署架构明确"U04 必须 ≥ U05 部署" | ✅ U04 已记 |

### 6.4 V1+ 升级路径
- **触发条件**：单 promotion 触发 ≥ 5 个监听器；或监听器涉及外部 API（如 U07 企微）
- **升级方案**：Outbox 模式（同事务写 outbox 表，异步 worker 投递到 Celery / Redis Streams）
- MVP 阶段（U04+U05）只有 1 个核心监听器（U05）+ 1 个反向通知 listener（U04 端 SettlementPaid），同事务足够

### 6.5 监控
- `settlement_created_via_event_total{result}` — handler 创建结果（FB1 强一致状态）
- 配 Sentry alert：`result="error"` rate > 1% 持续 5min → 通知后端 leader

---

## 7. SettlementPaid 反向事件容忍度（FB5）

### 7.1 事件分类
- `SettlementPaid` 标记 `required_handler = False`
- 仅 mark_paid 一个动作触发（FB5 简化）
- U04 端 listener 缺失 / 失败 → 不抛错，仅 log + 指标

### 7.2 失败容忍度
影响范围：
- 仅影响 U04 promotion.settlement_status 反向同步
- promotion.settlement_status 保持"待付款"而非"已付款"
- 用户在 U04 端看到 promotion 与 U05 端 settlement 短暂不一致

### 7.3 恢复机制
- **V1 引入 reconcile Celery beat 任务**（每天凌晨 03:00）：
  - 扫描 settlement_status="已付款" 但对应 promotion.settlement_status="待付款" 的 promotion
  - 批量同步推进到"已付款"
  - audit_log 记录 reconcile 数量（actor_type=system）
- MVP 阶段允许短暂不一致；以 settlement 为 source of truth

### 7.4 监控
- `settlement_paid_sync_no_match_total` Counter（promotion 端 UPDATE WHERE 0 行的次数）
- 阈值 > 5/min 持续 5min → Sentry warning（reconcile 任务激活前的指示器）

---

## 8. attachment GC 与引用计数（FB4）

### 8.1 V1 引用计数实施前置条件
- U05 mark_paid 时调 `AttachmentService.acquire_reference(attachment_id)` 增加引用计数
- attachment 表 `reference_count` 字段（U01 已设计，V1 启用）

### 8.2 V1 attachment GC 任务约束
- 扫描条件：`reference_count == 0 AND created_at < NOW() - INTERVAL '7 days'`
- **必须先**实现引用计数 + 测试 settlement 引用保护
- 单元测试：mock settlement.payment_proof_attachment_id 引用 → GC 不删除

### 8.3 MVP 阶段
- attachment GC 任务尚未实施（U01 只搭框架）
- settlement.payment_proof_attachment_id 引用的 attachment 不会被误删（GC 任务不存在）
- V1 实施前不引入引用计数（避免 attribute drift）

### 8.4 admin 极少手动作废
- admin 通过手动 SQL 修改 settlement 数据时不删 attachment 行
- 若需释放 attachment，admin 单独调 `AttachmentService.release_reference(attachment_id)`
- V2 order_adjustment 流程：保留旧 settlement + attachment 引用，新建 adjustment 行

---

## 9. 双口径汇总实现（FB7）

### 9.1 MVP 实现：直接 SQL（无 Materialized View）

#### 9.1.1 as_of 口径（GROUP BY simple）
```sql
SELECT
    settlement_status,
    COUNT(*) AS cnt,
    SUM(total_amount) AS sum_amt
FROM settlement
WHERE tenant_id = :tid
  AND created_at < :date + INTERVAL '1 day'
GROUP BY settlement_status;
```
- 走 `idx_settlement_tenant_status` (tenant_id, settlement_status, created_at)
- 10 万行单租户 P95 ≤ 100ms

#### 9.1.2 activity 口径（含 audit_log JOIN）
```sql
WITH
  newly_created AS (...),
  newly_approved AS (
    SELECT COUNT(DISTINCT al.resource_id) AS cnt,
           SUM(s.total_amount) AS sum_amt
    FROM audit_log al
    JOIN settlement s ON s.id::text = al.resource_id
    WHERE al.tenant_id = :tid
      AND al.action = 'settlement.review.approve'
      AND al.created_at >= :date AND al.created_at < :date + INTERVAL '1 day'
  ),
  newly_paid AS (
    SELECT COUNT(*) AS cnt, SUM(total_amount) AS sum_amt
    FROM settlement
    WHERE tenant_id = :tid
      AND payment_date = :date
  ),
  newly_rejected AS (...)
SELECT * FROM newly_created, newly_approved, newly_paid, newly_rejected;
```
- audit_log 走 audit_log.created_at 索引（U01 已建）
- 10 万 settlement + 1000 当天 action P95 ≤ 300ms

### 9.2 V1+ 升级触发条件
- as_of P95 > 200ms 持续 5min（不太可能，简单 GROUP BY）
- activity P95 > 500ms 持续 5min（更可能，audit_log 增长）
- 单租户 settlement > 10 万

### 9.3 V1+ 升级方案
引入 daily_settlement_activity Materialized View（按 (tenant_id, date) 预聚合，每小时刷新）：
```sql
CREATE MATERIALIZED VIEW daily_settlement_activity AS
SELECT
    tenant_id,
    DATE(created_at) AS activity_date,
    COUNT(*) FILTER (WHERE event = 'newly_created') AS created_count,
    SUM(total_amount) FILTER (WHERE event = 'newly_created') AS created_amount,
    ...
FROM (各事件 UNION ALL) all_events
GROUP BY tenant_id, DATE(created_at);

REFRESH MATERIALIZED VIEW CONCURRENTLY daily_settlement_activity;  -- 每小时
```

---

## 10. 监控指标

### 10.1 自定义 Prometheus 指标（5 个）

实现位置：`backend/app/core/metrics.py`（追加），与 U01-U04 共存。

```python
# 1. 状态机转移
settlement_state_transitions_total: Counter = Counter(
    "settlement_state_transitions_total",
    "Total settlement state machine transitions",
    labelnames=("from_state", "to_state"),
)

# 2. 事件创建幂等监控（FB1+FB3+FB6）
settlement_created_via_event_total: Counter = Counter(
    "settlement_created_via_event_total",
    "Total settlement creation outcomes via SettlementRequested handler",
    labelnames=("result",),  # created / duplicate_skipped / error
)

# 3. 序列号锁等待
settlement_sequence_lock_duration_seconds: Histogram = Histogram(
    "settlement_sequence_lock_duration_seconds",
    "Duration of settlement_sequence INSERT ON CONFLICT operation",
    buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
)

# 4. attachment 6 项校验失败分布（FB4）
attachment_validation_failures_total: Counter = Counter(
    "attachment_validation_failures_total",
    "Total attachment validation failures",
    labelnames=("failure_type", "source_module"),
    # failure_type ∈ {tenant_mismatch, bucket_invalid, purpose_invalid, mime_invalid, size_too_large, status_not_ready}
)

# 5. 反向同步丢失监控（FB5）
settlement_paid_sync_no_match_total: Counter = Counter(
    "settlement_paid_sync_no_match_total",
    "Total times U04 listener UPDATE promotion.settlement_status returned 0 rows",
)
```

### 10.2 Sentry tag

新增 `module=finance` tag 用于过滤 U05 异常。

### 10.3 告警阈值

| 触发条件 | 通道 | 接收方 |
|---|---|---|
| `histogram_quantile(0.95, http_request_duration_seconds{handler=~"/api/settlements.*"}) > 0.5` 持续 5min | Prometheus alertmanager | SRE |
| `rate(settlement_created_via_event_total{result="error"}[5m]) > 0.01` | Sentry → 即时 | 后端 leader（关键 — FB1 强一致失败） |
| `rate(attachment_validation_failures_total{failure_type="tenant_mismatch"}[5m]) > 0` | Sentry warning | 后端 + 安全 leader（潜在越权） |
| `rate(settlement_paid_sync_no_match_total[5m]) > 5/60` 持续 5min | Sentry warning | 后端 leader（FB5 反向事件丢失） |
| `histogram_quantile(0.95, settlement_sequence_lock_duration_seconds) > 0.5` | Prometheus | SRE |
| `/api/settlements.*` 5xx > 5% 持续 5min | Sentry | 后端 |

---

## 11. 数据迁移

### 11.1 历史 settlement 数据
- **不在 U05 阶段实施大规模迁移**
- MVP 启用后由 U06e（settlement 导入适配器）通过 Excel 模板批量上传
- 调用 `SettlementService.upsert_by_settlement_no()` 公共 API（按 settlement_no 幂等）

### 11.2 backfill migration（FB8 独立 008）
- U05 deploy 时立即执行 008（同一 alembic upgrade chain）
- 范围：U04 已 review approve 但 U05 未部署期间累积的 promotion（数量极少，FB1 强一致下理论 0 行）
- 状态：补"待核查"（与正常路径一致，FB2）
- settlement_no 通过 settlement_sequence 正常分配（FB8）

详见 `business-rules.md` BR-U05-10 + functional design plan §3.12 Q16。

### 11.3 迁移执行
- 通过 `migrate.yml` 专用 job 执行（U01 决策）
- 先 staging 后 production
- 失败回滚：008 downgrade 不可逆（财务数据保护），需 admin 手动审计后清理

U05 单次 migration 内容：
- 创建 3 张表（settlement / settlement_extra_item / settlement_sequence）
- 12 个索引（B-tree + GIN trgm，无 partial UNIQUE — FB3）
- 启用 RLS
- 008 backfill 历史数据

---

## 12. 测试覆盖

### 12.1 覆盖率门槛（与 U02/U03/U04 一致）
| 层 | 目标 |
|---|---|
| service.py | ≥ 80% |
| domain.py / state_machines.py | ≥ 90% |
| repository.py | ≥ 80% |
| api.py | ≥ 60% |

CI 强制 `--cov-fail-under=70`。

### 12.2 集成测试场景（34 项必覆盖）

#### 事件处理 / 三重幂等（FB1+FB3+FB6）
| # | 场景 | 关联 |
|---|---|---|
| 1 | EP06-S02 事件创建 settlement（settlement_status="待核查"） | EP06-S02 + FB1 |
| 2 | 三重幂等 1：同 promotion_id 重复事件 → no-op + audit duplicate_skipped | FB3 |
| 3 | 三重幂等 2：同 request_event_id 重投 → DB UNIQUE 阻止 + service SELECT 兜底 | FB3 |
| 4 | flush 立即暴露错误（mock IntegrityError → handler 抛错 + U04 事务回滚） | FB6 |
| 5 | U05 listener 缺失（clear_handlers → U04 dispatch 抛 MissingRequiredHandlerError → review approve 失败回滚） | FB1 |
| 6 | handler 抛异常 → 跨单元事务回滚（mock listener 抛 RuntimeError → promotion 也回滚） | FB1 |

#### 状态机（FB7 模式）
| # | 场景 | 关联 |
|---|---|---|
| 7 | EP06-S03 待核查 + approve → 待付款 | EP06-S03 |
| 8 | EP06-S04 reject + reason → 已驳回 | EP06-S04 |
| 9 | 自审禁止（settlement.pr_id == reviewer.id → 403） | BR-U05-22 |
| 10 | reject 缺 reason → 422 | EP06-S04 |
| 11 | 状态推进并发（100 并发 mark_paid 同 settlement → 1 成功 99 冲突） | FB7 |
| 12 | 跨租户状态推进（A 租户 settlement，B 租户用户 mark_paid → UPDATE 0 行 → 409） | FB7 |

#### extra_item
| # | 场景 | 关联 |
|---|---|---|
| 13 | EP06-S05 增加 extra_item → total_amount 重算 | EP06-S05 |
| 14 | EP06-S05 非 PR 主管 → 403 | BR-U05-42 |
| 15 | settlement_status != 待付款 → 422 | BR-U05-40 |

#### fill_payment
| # | 场景 | 关联 |
|---|---|---|
| 16 | EP06-S06 待付款 + payment_amount → 待财务付款 | EP06-S06 |

#### mark_paid + attachment 强校验（FB4）
| # | 场景 | 关联 |
|---|---|---|
| 17 | EP06-S07 完整流程：上传 attachment → mark_paid → 已付款 | EP06-S07 |
| 18 | 缺字段（payment_date / attachment_id 任一）→ 422 | EP06-S07 |
| 19 | 已付款重复上传 → 409 | EP06-S07 |
| 20 | attachment 跨租户 → 422 + Sentry warning + audit | FB4 |
| 21 | attachment bucket != private → 422 | FB4 |
| 22 | attachment purpose != settlement_proof → 422 | FB4 |
| 23 | attachment mime 不在白名单 → 422 | FB4 |
| 24 | attachment status != ready → 422 | FB4 |

#### 双口径汇总（FB7）
| # | 场景 | 关联 |
|---|---|---|
| 25 | daily-summary/activity（当日 created/approved/paid/rejected 计数 + 金额） | EP06-S08 + FB7 |
| 26 | daily-summary/as-of（截至当日按 status GROUP BY + outstanding_total） | EP06-S08 + FB7 |

#### 反向事件（FB5）
| # | 场景 | 关联 |
|---|---|---|
| 27 | mark_paid 发 SettlementPaid → U04 listener 同步 promotion.settlement_status='已付款' | FB5 |
| 28 | U04 listener 缺失 → SettlementPaid no-op（不抛错，required_handler=False） | FB5 |

#### 财务记录不可替换（FB3）
| # | 场景 | 关联 |
|---|---|---|
| 29 | DELETE /api/settlements/{id} → 405 | FB3 |
| 30 | promotion 软删时 settlement 不级联 | FB3 |

#### 性能 + 多租户
| # | 场景 | 关联 |
|---|---|---|
| 31 | 性能基准 `test_settlement_list_perf_with_10k_records` | NFR §3 |
| 32 | 多租户隔离回归（A 租户用户看不到 B 租户 settlement） | EP01-S07 |
| 33 | settlement_no 9999 溢出 → 500 | BR-U05-02 |

#### 端到端
| # | 场景 | 关联 |
|---|---|---|
| 34 | EP06 完整 J4：U04 review approve → U05 settlement 创建 → approve → fill_payment → upload_proof → mark_paid → SettlementPaid → U04 promotion 同步 | 端到端 |

---

## 13. 一致性校验

| 校验 | 结果 |
|---|---|
| 容量基线与 U04 promotion 1:1 对齐 | ✅ |
| 性能 SLA 与 U04 review approve 链路总和不超 500ms | ✅ |
| 索引清单 12 个全部 B-tree + GIN trgm + 永久 UNIQUE（FB3） | ✅ |
| 字段权限 3 类（PAYMENT_VISIBLE / WRITABLE / PROOF_UPLOAD） | ✅ |
| 跨单元事务一致性继承 U04 FB1 决策 | ✅ |
| handler flush 立即暴露错误（FB6） | ✅ |
| 三重幂等 + 永久 UNIQUE（FB3） | ✅ |
| attachment 6 项强校验 + 跨租户监控（FB4） | ✅ |
| 反向事件 SettlementPaid 容忍度 + V1 reconcile 路径（FB5） | ✅ |
| 双口径汇总 SLA + V1 升级路径（FB7） | ✅ |
| audit 脱敏含 attachment_id_changed 标记（FB3+FB4） | ✅ |
| MVP 不引入 attachment 引用计数；GC 任务尚未启动 | ✅ |
| 财务记录不可替换审计追溯（FB3） | ✅ |
| 5 个自定义 Prometheus 指标 | ✅ |
| 34 个集成测试场景覆盖 FB1-FB8 全部 | ✅ |
| 与 U06e / U09 / U14 / U16 演化预留 | ✅ |
