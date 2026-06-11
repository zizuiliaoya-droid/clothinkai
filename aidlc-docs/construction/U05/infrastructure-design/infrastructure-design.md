# U05 基础设施设计（Infrastructure Design）

> 单元：U05 — 财务结款核心  
> 范围：U05 特异性基础设施增量；通用基础设施全部继承 U01 + shared-infrastructure  
> 关键差异：**首次使用 R2 private 桶**（付款截图，FB4）

---

## 1. 资源清单（U05 增量）

### 1.1 无新增 Zeabur / 域名 / 证书 / Secrets

继承 U01 完整 6 服务部署：
- backend / frontend / celery-worker / celery-beat / postgres / redis（已就绪）
- 域名：app.clothinkai.com / api.clothinkai.com / app-staging.clothinkai.com / api-staging.clothinkai.com（已就绪）
- 环境变量：与 U01 完全一致，无新增

### 1.2 PostgreSQL 增量

| 资源 | 类型 | 说明 |
|---|---|---|
| `settlement` | 表 | 22 字段，**无 is_active**（FB3） |
| `settlement_extra_item` | 表 | 8 字段（含 created_by） |
| `settlement_sequence` | 表 | 4 字段（与 promotion_sequence 同模式） |
| 12 个索引 | 索引 | 详见 §1.2.2 |
| 2 RLS 策略 | RLS | settlement / settlement_extra_item（settlement_sequence 不需要 RLS — 内部表，仅后端访问） |

#### 1.2.1 表 DDL 摘要（详见 deployment-architecture.md 完整 migration）

```sql
-- settlement 表（注意：无 is_active 字段，FB3）
CREATE TABLE settlement (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenant(id) ON DELETE RESTRICT,
    promotion_id UUID NOT NULL REFERENCES promotion(id) ON DELETE RESTRICT,
    blogger_id UUID NOT NULL REFERENCES blogger(id) ON DELETE RESTRICT,
    style_id UUID NOT NULL REFERENCES style(id) ON DELETE RESTRICT,
    pr_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
    reviewed_by UUID REFERENCES "user"(id) ON DELETE SET NULL,
    paid_by UUID REFERENCES "user"(id) ON DELETE SET NULL,
    payment_proof_attachment_id UUID REFERENCES attachment(id) ON DELETE RESTRICT,
    settlement_no VARCHAR(64) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount >= 0),
    total_amount NUMERIC(12, 2) NOT NULL CHECK (total_amount >= 0),
    payment_amount NUMERIC(12, 2) CHECK (payment_amount IS NULL OR payment_amount >= 0),
    payment_date DATE,
    note_title VARCHAR(255),
    remark TEXT,
    settlement_status VARCHAR(16) NOT NULL DEFAULT '待核查',
    reviewed_at TIMESTAMPTZ,
    review_action VARCHAR(16),
    review_reason TEXT,
    request_event_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- settlement_extra_item 表
CREATE TABLE settlement_extra_item (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenant(id) ON DELETE RESTRICT,
    settlement_id UUID NOT NULL REFERENCES settlement(id) ON DELETE CASCADE,
    item_type VARCHAR(16) NOT NULL,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    remark VARCHAR(255),
    created_by UUID REFERENCES "user"(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- settlement_sequence 表
CREATE TABLE settlement_sequence (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL REFERENCES tenant(id) ON DELETE RESTRICT,
    date_key DATE NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT 0
        CHECK (last_seq >= 0 AND last_seq <= 9999),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### 1.2.2 索引清单（12 个）

```sql
-- settlement 表
CREATE UNIQUE INDEX uq_settlement_no ON settlement (tenant_id, settlement_no);
CREATE UNIQUE INDEX uq_settlement_promotion ON settlement (tenant_id, promotion_id);
-- 注意：永久 UNIQUE，无 partial WHERE（FB3）
CREATE UNIQUE INDEX uq_settlement_request_event_id ON settlement (request_event_id);

CREATE INDEX idx_settlement_tenant_status ON settlement (tenant_id, settlement_status, created_at DESC);
CREATE INDEX idx_settlement_blogger ON settlement (tenant_id, blogger_id);
CREATE INDEX idx_settlement_style ON settlement (tenant_id, style_id);
CREATE INDEX idx_settlement_pr ON settlement (tenant_id, pr_id);
CREATE INDEX idx_settlement_payment_date ON settlement (tenant_id, payment_date);
CREATE INDEX idx_settlement_reviewed_by ON settlement (tenant_id, reviewed_by);
CREATE INDEX idx_settlement_paid_by ON settlement (tenant_id, paid_by);
CREATE INDEX idx_settlement_no_trgm ON settlement USING gin (settlement_no gin_trgm_ops);

-- settlement_extra_item 表
CREATE INDEX idx_extra_item_settlement ON settlement_extra_item (tenant_id, settlement_id);

-- settlement_sequence 表
CREATE UNIQUE INDEX uq_settlement_sequence ON settlement_sequence (tenant_id, date_key);
```

#### 1.2.3 RLS 策略

```sql
-- 启用 RLS
ALTER TABLE settlement ENABLE ROW LEVEL SECURITY;
ALTER TABLE settlement_extra_item ENABLE ROW LEVEL SECURITY;

-- 沿用 U01 的 RLS 模板（按 current_setting('app.tenant_id') 过滤）
CREATE POLICY tenant_isolation ON settlement
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

CREATE POLICY tenant_isolation ON settlement_extra_item
    USING (tenant_id = current_setting('app.tenant_id', true)::uuid);

-- bypass 角色策略（与 U01 风格一致）
CREATE POLICY bypass_rls ON settlement
    FOR ALL TO clothing_bypass
    USING (true);

CREATE POLICY bypass_rls ON settlement_extra_item
    FOR ALL TO clothing_bypass
    USING (true);

-- settlement_sequence 不启用 RLS（内部表，仅后端 INSERT ON CONFLICT，无跨租户暴露风险）
```

### 1.3 R2 增量（首次使用 private 桶，FB4）

#### 1.3.1 路径规划

```
{bucket_private}/
└── {tenant_id}/
    └── settlements/
        └── proof/
            └── {attachment_id}/
                └── {filename}
```

具体生成由 U01 AttachmentService 完成（U05 不直接操作 R2 path）。

#### 1.3.2 Bucket Policy（继承 U01，无变更）
- 仅后端服务可访问（通过 IAM access key）
- 公网无法直接访问
- 通过 signed URL 才能下载（R2 native signing）
- TTL：15 分钟（900 秒）

#### 1.3.3 Attachment 6 项约束（service 层 BR-U05-60 强制）
- bucket = "private"
- purpose = "settlement_proof"
- mime ∈ {image/jpeg, image/png, image/webp, application/pdf}
- size ≤ 10 MB
- status = "ready"
- tenant_id = user.tenant_id

详见 nfr-design-patterns.md P-U05-02。

### 1.4 Sentry 增量

#### 1.4.1 新增 tag
- `module=finance` — 用于过滤 U05 模块异常
- `event_type=SettlementRequested` / `event_type=SettlementPaid` — 事件分发上下文

#### 1.4.2 告警路由（继承 U01 alert rules + 新增）

| 告警 | 通道 | 接收方 |
|---|---|---|
| `module=finance` 5xx > 5% 持续 5min | Sentry → 即时 | 后端 leader |
| `event_type=SettlementRequested` + `result=error` rate > 1% | Sentry → 即时 | 后端 leader（FB1 强一致失败） |
| `tag:source_module=finance` + `level=warning` + message="potential_cross_tenant_attempt" | Sentry → 即时 | **后端 leader + 安全 leader 抄送** |
| `event_type=SettlementPaid` 失败 + `settlement_paid_sync_no_match_total` rate > 5/min | Sentry → warning | 后端 leader（FB5 反向事件丢失） |

#### 1.4.3 traces sample rate（继承 U01）
- production：10%
- staging：100%

### 1.5 Prometheus 增量（5 个指标）

```python
# core/metrics.py（追加）
settlement_state_transitions_total: Counter
settlement_created_via_event_total: Counter        # 含 created/duplicate_skipped/error
settlement_sequence_lock_duration_seconds: Histogram
attachment_validation_failures_total: Counter      # 含 6 类 failure_type
settlement_paid_sync_no_match_total: Counter
```

### 1.6 alertmanager 规则（追加 6 条）

```yaml
# config/alertmanager/u05-finance.yaml
groups:
- name: u05-finance
  rules:
  - alert: SettlementApiSlow
    expr: histogram_quantile(0.95, http_request_duration_seconds{handler=~"/api/settlements.*"}) > 0.5
    for: 5m
    labels: { severity: warning, module: finance }
    annotations:
      summary: "U05 settlement API P95 > 500ms"
  
  - alert: SettlementCreationFailed
    expr: rate(settlement_created_via_event_total{result="error"}[5m]) > 0.01
    for: 1m
    labels: { severity: critical, module: finance }
    annotations:
      summary: "FB1 强一致失败：U04 review approve → U05 settlement 创建错误率 > 1%"
  
  - alert: AttachmentCrossTenantAttempt
    expr: rate(attachment_validation_failures_total{failure_type="tenant_mismatch"}[5m]) > 0
    for: 1m
    labels: { severity: warning, module: finance, security: true }
    annotations:
      summary: "潜在跨租户 attachment 访问尝试"
  
  - alert: SettlementPaidSyncFailed
    expr: rate(settlement_paid_sync_no_match_total[5m]) > 0.083  # 5/min
    for: 5m
    labels: { severity: warning, module: finance }
    annotations:
      summary: "FB5 反向同步频繁 0 行匹配（reconcile 任务激活前指示器）"
  
  - alert: SettlementSequenceLockSlow
    expr: histogram_quantile(0.95, settlement_sequence_lock_duration_seconds) > 0.5
    for: 5m
    labels: { severity: warning, module: finance }
    annotations:
      summary: "U05 settlement_sequence 序列号锁等待 P95 > 500ms"
  
  - alert: SettlementErrorRate
    expr: rate(http_requests_total{handler=~"/api/settlements.*", status=~"5.."}[5m])
        / rate(http_requests_total{handler=~"/api/settlements.*"}[5m]) > 0.05
    for: 5m
    labels: { severity: critical, module: finance }
    annotations:
      summary: "U05 API 5xx 比例 > 5%"
```

---

## 2. 服务拓扑（继承 U01，无变更）

```
┌─────────────────────────────────────────────────────────────┐
│                       Cloudflare CDN                         │
└────┬───────────────────────────────────────┬─────────────────┘
     │                                       │
     ▼                                       ▼
  frontend (nginx)                     backend (FastAPI)
                                            │
                  ┌─────────────────────────┼─────────────────────┐
                  ▼                         ▼                     ▼
            postgres (16)              redis (7)            R2 (4 桶)
              ↑↓                         ↑↓                   ↑↓
        celery-worker              celery-beat       (private 桶 U05 首次使用)
                                    (V1 finance_reconcile 任务)
```

U05 不增加服务。

---

## 3. 部署一致性约束（继承 U04 FB10 + U05 启用 e2e-smoke）

### 3.1 5 层防护

| 层 | 防护内容 | U05 状态 |
|---|---|---|
| 1. PR 层 | U04 + U05 代码 + migration 同 PR / 强相关 PR 顺序 | 文档约束（开发流程） |
| 2. CI 层 | grep `from app.modules.finance.listeners import register` 必须命中 | ✅ U04 batch 4 已实施 |
| 3. Migration 层 | `alembic upgrade head` 一次性升 007 + 008 | ✅ migrate.yml 通用 job |
| 4. Smoke 层 | staging deploy 后跑 e2e-smoke 真实端到端测试（验证 FB1） | **U05 实施时启用**（U04 是 placeholder） |
| 5. Startup 层 | register_finance fail fast；register_promotion_listeners 失败也 fail fast | ✅ U04 batch 4 已搭框架 |

### 3.2 production 部署强约束

deploy-staging.yml::e2e-smoke-after-deploy 失败 → 阻止 production 部署。

详见 deployment-architecture.md §4。

---

## 4. 与 shared-infrastructure 对齐

### 4.1 Attachment 引用计数（V1 实施前置条件）

shared-infrastructure 决策：
- attachment 表 V1 启用 `reference_count` 字段
- attachment GC 任务 V1 实施时**必须先**实现引用计数 + 测试 settlement.payment_proof_attachment_id 引用保护

U05 实施时不引入引用计数（attribute drift 风险），但代码留 TODO V1 标记：

```python
# modules/finance/service.py
async def upload_payment_proof(self, ...):
    # ... attachment 6 项校验 + UPDATE WHERE
    # TODO V1: 调 AttachmentService.acquire_reference(attachment_id)
    ...
```

### 4.2 R2 4 桶设计（U01 已建）

```
{bucket_public}/      # 商品图 / 设计稿等公开资源
{bucket_private}/     # 付款截图等敏感资源（U05 首次使用）
{bucket_credentials}/ # 加密的平台凭据备份
{bucket_backups}/     # 数据库备份
```

U05 不修改桶定义，仅消费 `{bucket_private}`。

---

## 5. 一致性校验

| 校验 | 结果 |
|---|---|
| 无新增 Zeabur / 域名 / 证书 / Secrets / 环境变量 | ✅ |
| PG 增量：3 张表 + 12 索引 + 2 RLS（永久 UNIQUE，FB3） | ✅ |
| R2 private 桶首次消费 + 无 policy 变更 | ✅ |
| signed URL TTL 与 U01 baseline 一致（15min） | ✅ |
| Sentry 新增 tag + 4 条告警规则（FB1+FB4+FB5+性能） | ✅ |
| Prometheus 5 个指标（与 NFR Requirements 对齐） | ✅ |
| 部署一致性 5 层防护（继承 U04） | ✅ |
| Attachment 引用计数 V1 路径预留 | ✅ |
