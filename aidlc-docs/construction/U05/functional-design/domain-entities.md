# U05 领域实体（Domain Entities）

> 单元：U05 — 财务结款核心  
> 范围：Settlement + SettlementExtraItem + SettlementSequence + 1 领域事件 + 4 Python Enum  
> 不含：Settlement.is_active 字段（FB3：财务记录永久不可替换）；OrderAdjustment / Balance（U16 V2）

---

## 1. 实体清单

| # | 实体 | 类型 | 多租户 | 说明 |
|---|---|---|---|---|
| 1 | `Settlement` | TenantScopedModel | ✅ | 结算单（财务核心表） |
| 2 | `SettlementExtraItem` | TenantScopedModel | ✅ | 结算附加项（运费 / 赞奖 / 其他） |
| 3 | `SettlementSequence` | TenantScopedModel | ✅ | settlement_no 序列号表（按 (tenant_id, date_key) 计数） |
| 4 | `SettlementStatus` | Python Enum | — | 5 状态（待核查 / 待付款 / 待财务付款 / 已付款 / 已驳回） |
| 5 | `ExtraItemType` | Python Enum | — | 3 值（运费 / 赞奖 / 其他） |
| 6 | `ReviewAction` | Python Enum | — | 复用 U04 promotion.enums.ReviewAction |
| 7 | `Platform` | Python Enum | — | 复用 U03 blogger.enums.Platform |

衍生字段（不持久化）：
- `total_amount`（service 层维护：`amount + SUM(extra_items.amount)`），但**持久化于 settlement.total_amount**作为快照
- `is_overdue`（V1+ 按 created_at 计算未付款超时）— 当前 MVP 不实现
- `outstanding`（as-of 汇总按 status 维度聚合，不在单条响应里）

领域事件：
- `SettlementPaid`（U05 → U04 反向通知，**通知类**`required_handler=False`，仅 mark_paid 时触发，FB5）

---

## 2. ER 图（Mermaid）

```mermaid
erDiagram
    Tenant ||--o{ Settlement : owns
    Tenant ||--o{ SettlementExtraItem : owns
    Tenant ||--o{ SettlementSequence : owns
    Promotion ||--|| Settlement : "promotion_id 永久 1:1"
    Style ||--o{ Settlement : "style_id 冗余"
    Blogger ||--o{ Settlement : "blogger_id 冗余"
    User ||--o{ Settlement : "pr_id 冗余 from promotion"
    User ||--o{ Settlement : "reviewed_by"
    User ||--o{ Settlement : "paid_by"
    Attachment ||--o{ Settlement : "payment_proof_attachment_id"
    Settlement ||--o{ SettlementExtraItem : "settlement_id ON DELETE CASCADE"

    Settlement {
        UUID id PK
        UUID tenant_id FK
        UUID promotion_id FK_UNIQUE_永久
        UUID blogger_id FK
        UUID style_id FK
        UUID pr_id FK
        UUID reviewed_by FK_optional
        UUID paid_by FK_optional
        UUID payment_proof_attachment_id FK_optional
        string settlement_no "UNIQUE per tenant"
        decimal amount "DECIMAL(12,2)"
        decimal total_amount "DECIMAL(12,2)"
        decimal payment_amount "DECIMAL(12,2) optional"
        date payment_date "optional"
        string note_title "optional"
        text remark "optional"
        string settlement_status "5 状态"
        timestamp reviewed_at
        string review_action
        text review_reason
        UUID request_event_id "UNIQUE 永久"
        timestamp created_at
        timestamp updated_at
    }

    SettlementExtraItem {
        UUID id PK
        UUID tenant_id FK
        UUID settlement_id FK_CASCADE
        string item_type "3 值"
        decimal amount "DECIMAL(12,2) > 0"
        string remark "optional"
        UUID created_by FK_optional
        timestamp created_at
        timestamp updated_at
    }

    SettlementSequence {
        UUID id PK
        UUID tenant_id FK
        date date_key
        int last_seq "0..9999"
    }
```

---

## 3. Settlement 字段详细

### 3.1 关联字段

| 字段 | 类型 | 必填 | FK / 引用 | 说明 |
|---|---|---|---|---|
| `id` | UUID | ✅ | — | 主键 |
| `tenant_id` | UUID | ✅ | tenant.id | 继承 TenantScopedModel |
| `promotion_id` | UUID | ✅ | promotion.id ON DELETE RESTRICT | 必关联推广；**永久 UNIQUE per tenant**（FB3） |
| `blogger_id` | UUID | ✅ | blogger.id ON DELETE RESTRICT | 冗余，便于查询 |
| `style_id` | UUID | ✅ | style.id ON DELETE RESTRICT | 冗余，便于按款式聚合 |
| `pr_id` | UUID \| null | ❌ | user.id ON DELETE SET NULL | 冗余 from promotion |
| `reviewed_by` | UUID \| null | ❌ | user.id ON DELETE SET NULL | settlement 端核查人 |
| `paid_by` | UUID \| null | ❌ | user.id ON DELETE SET NULL | 财务付款上传人 |
| `payment_proof_attachment_id` | UUID \| null | ❌ | attachment.id ON DELETE RESTRICT | **FB4**：通过 attachment 表引用，不存裸 R2 key |

### 3.2 业务键

| 字段 | 类型 | 必填 | 唯一 | 说明 |
|---|---|---|---|---|
| `settlement_no` | VARCHAR(64) | ✅ | UNIQUE (tenant_id, settlement_no) | 格式 `<tenant_prefix>S<yyMMdd><0001>`，按 (tenant_id, date_key) 当天累加 |

### 3.3 金额字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `amount` | DECIMAL(12,2) | ✅ | 基础金额（来自 SettlementRequested.amount = promotion.quote_amount） |
| `total_amount` | DECIMAL(12,2) | ✅ | 含 extra_items 的合计金额（service 层维护：`amount + SUM(extra_items.amount)`） |
| `payment_amount` | DECIMAL(12,2) \| null | ❌ | PR 主管确认的付款金额（可能与 total_amount 不同：抹零、汇率） |

CHECK 约束：
- `amount >= 0`
- `total_amount >= 0`
- `payment_amount IS NULL OR payment_amount >= 0`

### 3.4 业务字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `payment_date` | DATE | ❌ | 财务填写；mark_paid 时必填且 ≤ today |
| `note_title` | VARCHAR(255) | ❌ | 笔记标题（冗余 from promotion） |
| `remark` | TEXT | ❌ | 备注 |

### 3.5 状态字段（FB1：5 主状态）

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `settlement_status` | VARCHAR(16) | ✅ | `'待核查'` | 5 状态见 §6.1 |

**关键语义脱钩（FB1）**：
- `settlement.settlement_status` 是 U05 内部状态机
- `promotion.settlement_status`（U04 字段）是 U04 端的指示器
- 两者**语义独立**，不直接对齐；仅 mark_paid 通过 SettlementPaid 反向同步 promotion 端到"已付款"

### 3.6 审核相关

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reviewed_at` | TIMESTAMPTZ | ❌ | 审核时间 |
| `review_action` | VARCHAR(16) | ❌ | approve / reject |
| `review_reason` | TEXT | ❌ | reject 时必填 |

### 3.7 事件溯源

| 字段 | 类型 | 必填 | 唯一 | 说明 |
|---|---|---|---|---|
| `request_event_id` | UUID | ✅ | UNIQUE 永久 | 来自 SettlementRequested.event_id；事件重放防护 |

> 该字段非空 + 永久 UNIQUE。即使 backfill（无原始事件可追溯），也合成新 UUID 填入（migration 008 实施）。

### 3.8 通用字段

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `created_at` | TIMESTAMPTZ | `now()` | 继承 TenantScopedModel |
| `updated_at` | TIMESTAMPTZ | `now()` | 继承 |

> **重要（FB3）**：U05 settlement 表**不设 `is_active` 字段**。
> 财务记录永久不可替换：错误付款修正在 V2 通过 order_adjustment 调整单实现。

---

## 4. SettlementExtraItem 字段详细

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | UUID PK | ✅ | — |
| `tenant_id` | UUID FK | ✅ | 继承 TenantScopedModel |
| `settlement_id` | UUID FK | ✅ | settlement.id ON DELETE CASCADE |
| `item_type` | VARCHAR(16) | ✅ | ExtraItemType 枚举 |
| `amount` | DECIMAL(12,2) | ✅ | CHECK > 0 |
| `remark` | VARCHAR(255) | ❌ | 备注 |
| `created_by` | UUID \| null | ❌ | user.id ON DELETE SET NULL — 哪个 PR 主管添加 |
| `created_at` | TIMESTAMPTZ | ✅ | — |
| `updated_at` | TIMESTAMPTZ | ✅ | — |

约束：
- `amount > 0`（CHECK 约束）
- service 层维护 `settlement.total_amount = settlement.amount + SUM(extra_items.amount)`
- 仅 settlement_status="待付款" 时允许新增 / 修改 extra_item（BR-U05-31）
- 删除 settlement 时级联删除 extra_items（ON DELETE CASCADE，但 settlement 实际上 MVP 不可删，所以基本不会触发）

---

## 5. SettlementSequence 字段（settlement_no 序列号表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | — |
| `tenant_id` | UUID FK | — |
| `date_key` | DATE | 与 SettlementRequested.requested_at::date 同 |
| `last_seq` | INTEGER | 最后使用的序列号（0..9999） |
| `created_at` / `updated_at` | TIMESTAMPTZ | — |

**唯一约束**：`UNIQUE (tenant_id, date_key)`

**生成流程**（复用 U04 FB2 模式 — INSERT ON CONFLICT DO UPDATE RETURNING）：
```sql
INSERT INTO settlement_sequence (id, tenant_id, date_key, last_seq, created_at, updated_at)
VALUES (gen_random_uuid(), :tid, :dk, 1, NOW(), NOW())
ON CONFLICT (tenant_id, date_key) DO UPDATE
SET last_seq = settlement_sequence.last_seq + 1, updated_at = NOW()
RETURNING last_seq;
```

防 race condition：单条 SQL 原子，无需 SELECT FOR UPDATE 悲观锁（与 U04 PromotionSequence 完全一致）。

CHECK 约束：
- `last_seq >= 0`
- `last_seq <= 9999`（超过抛 SequenceOverflowError）

---

## 6. 索引清单

### 6.1 settlement 表（FB3 + FB7）

| 索引 | 类型 | 列 / 表达式 | 用途 |
|---|---|---|---|
| `uq_settlement_no` | B-tree UNIQUE | `(tenant_id, settlement_no)` | 业务键唯一 |
| `uq_settlement_promotion` | B-tree UNIQUE | `(tenant_id, promotion_id)` | **永久幂等键（FB3：财务记录不可替换）** |
| `uq_settlement_request_event_id` | B-tree UNIQUE | `(request_event_id)` | 事件重放防护 |
| `idx_settlement_tenant_status` | B-tree | `(tenant_id, settlement_status, created_at DESC)` | 列表过滤 + as_of 汇总（FB7） |
| `idx_settlement_blogger` | B-tree | `(tenant_id, blogger_id)` | 按博主查 |
| `idx_settlement_style` | B-tree | `(tenant_id, style_id)` | 按款式聚合 |
| `idx_settlement_pr` | B-tree | `(tenant_id, pr_id)` | 按 PR 筛选 |
| `idx_settlement_payment_date` | B-tree | `(tenant_id, payment_date)` | activity 汇总（FB7） |
| `idx_settlement_reviewed_by` | B-tree | `(tenant_id, reviewed_by)` | 按审核人查 |
| `idx_settlement_paid_by` | B-tree | `(tenant_id, paid_by)` | 按付款人查 |
| `idx_settlement_no_trgm` | GIN trgm | `(settlement_no gin_trgm_ops)` | 关键字搜索（无 partial：所有 settlement 都活跃） |

### 6.2 settlement_extra_item 表

| 索引 | 类型 | 列 |
|---|---|---|
| `idx_extra_item_settlement` | B-tree | `(tenant_id, settlement_id)` |
| `idx_extra_item_type` | B-tree | `(tenant_id, item_type)` — V1 类型聚合预留 |

### 6.3 settlement_sequence 表

| 索引 | 类型 | 列 |
|---|---|---|
| `uq_settlement_sequence` | B-tree UNIQUE | `(tenant_id, date_key)` |

---

## 6. 枚举定义

### 6.1 SettlementStatus（5 状态，FB1）
```python
class SettlementStatus(str, Enum):
    PENDING_REVIEW = "待核查"           # 起点（SettlementRequested handler 创建时）
    PENDING_PAYMENT = "待付款"          # PR 主管 approve 后
    PENDING_FINANCE = "待财务付款"      # PR 主管 fill_payment 后
    PAID = "已付款"                     # 财务 mark_paid 后（终态）
    REJECTED = "已驳回"                 # PR 主管 reject 后（可 resubmit 回到 PENDING_REVIEW）
```

### 6.2 ExtraItemType
```python
class ExtraItemType(str, Enum):
    SHIPPING = "运费"
    REWARD = "赞奖"
    OTHER = "其他"
```

### 6.3 ReviewAction（复用 U04 promotion.enums）
```python
# 直接 from app.modules.promotion.enums import ReviewAction
class ReviewAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
```

### 6.4 Platform（复用 U03 blogger.enums）

---

## 7. 领域事件

### 7.1 监听的事件（来自 U04）

```python
# 来自 modules/promotion/events.py（U04 已落地）
@dataclass(frozen=True)
class SettlementRequested:
    event_type: ClassVar[str] = "SettlementRequested"
    required_handler: ClassVar[bool] = True  # 强一致
    
    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    style_id: UUID
    amount: Decimal
    requested_by: UUID
    requested_at: datetime
```

**处理流程**（详见 business-rules.md BR-U05-10）：
1. handler 注册位置：`modules/finance/listeners.py`
2. 同事务执行（与 U04 service.review approve 共享 session）
3. **三重幂等**：UNIQUE(tenant_id, promotion_id) + UNIQUE(request_event_id) + service 层 SELECT
4. 生成 settlement_no（序列号原子分配）
5. 创建 settlement，settlement_status="**待核查**"（FB1）
6. **`await session.flush()`**（FB6：UNIQUE / FK 错误立即暴露）
7. 写 audit `settlement.create_via_event` 或 `settlement.create_skipped_duplicate`

### 7.2 发出的事件（U05 → U04）

```python
# modules/finance/events.py（U05 新建）
@dataclass(frozen=True)
class SettlementPaid:
    """U05 → U04 反向通知：mark_paid 时同步 promotion.settlement_status='已付款'。
    
    通知类（required_handler=False）：U04 端 listener 缺失不影响 U05 主流程。
    """
    event_type: ClassVar[str] = "SettlementPaid"
    required_handler: ClassVar[bool] = False
    
    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    settlement_id: UUID
    promotion_id: UUID
    payment_amount: Decimal
    payment_date: date
    paid_by: UUID
```

**反向同步策略（FB5）**：
- MVP **仅 mark_paid 一个动作**反向同步
- U04 端 listener 监听 SettlementPaid → UPDATE promotion SET settlement_status='已付款' WHERE id=... AND tenant_id=... AND settlement_status='待付款' AND is_active=true
- 其他状态推进（reject / fill_payment / resubmit）以 settlement 为 source of truth，不反向同步

> MVP 阶段不引入 SettlementRejected / SettlementResubmitted 等反向事件。
> V1 视用户反馈再评估。

---

## 8. 与 U01-U04 的关系

| 关系 | 来源 | 引用方式 |
|---|---|---|
| `tenant_id` | U01 | 继承 TenantScopedModel |
| `promotion_id` | U04 | FK + 永久 UNIQUE per tenant |
| `style_id` / `blogger_id` | U02 / U03 | FK 冗余存（便于按款式 / 博主聚合查询） |
| `pr_id` / `reviewed_by` / `paid_by` | U01 | FK 到 user.id |
| `payment_proof_attachment_id` | U01（attachment） | FK + 6 项强校验（FB4） |
| 状态机基类 | U01 `core/state_machine.py` | 与 U04 同模式：classmethod assert_can_transition + get_allowed_transitions |
| 审计 | U01 | `AuditService.log` 显式调用（与 U04 一致） |
| 权限装饰器 | U01 | `@require_permission("settlement:read/write/review/pay")` |
| 字段权限模式 | U02/U03/U04 | 复用 legacy_field_permissions 模式：PAYMENT_VISIBLE / WRITABLE / PROOF_UPLOAD（U09 后清理） |
| 审计敏感值脱敏 | U02/U03/U04 | 复用 `*_changed: true` 标记策略 |
| 序列号原子分配 | U04 PromotionSequence | 完全相同模式：INSERT ON CONFLICT DO UPDATE RETURNING |
| 事件总线 | U04 引入的 core/events.py | subscribe / dispatch / clear_handlers |
| AttachmentService | U01 | get_by_id / get_signed_url（FB4） |

---

## 9. 演化路线图

| 阶段 | 单元 | 演化项 |
|---|---|---|
| **MVP** | U05（本单元） | 上述全部 |
| **MVP** | U06e | SettlementImportAdapter（按 settlement_no 幂等导入历史结算单） |
| **MVP** | U08 | （未来）pending_payment 待付款金额监控指标 |
| **V1** | U09 | 字段级权限改造：PAYMENT_VISIBLE_ROLES → Permission.field_filter() |
| **V1** | U13 | （无影响）数据采集 Worker 不直接操作 settlement |
| **V1** | U14 | 投产报表按 settlement.payment_amount 计算 ROI（基础口径，无 exclude_brushing） |
| **V2** | U16 | order_adjustment 调整单：处理已付款 settlement 的金额修正 / 退款 / 错付（替代"删除重建"） |
| **V2** | U16 | 投产报表 ROI 计算加入 `exclude_brushing=true` 默认，剔除刷单订单 |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 继承 TenantScopedModel | ✅ |
| 强依赖关系明确（U01 + U04） | ✅ |
| 业务键 settlement_no 永久 UNIQUE | ✅ |
| promotion_id 永久 UNIQUE（FB3） | ✅ |
| 不设 is_active 字段（FB3） | ✅ |
| payment_proof 通过 attachment 表引用（FB4） | ✅ |
| settlement_status 起点统一 = 待核查（FB1） | ✅ |
| 反向事件仅 SettlementPaid（FB5） | ✅ |
| 序列号 (tenant_id, date_key) UNIQUE | ✅ |
| 索引覆盖性能 SLA 路径（含 as-of / activity 双口径，FB7） | ✅ |
| 与 U06e / U09 / U14 / U16 演化预留 | ✅ |
