# U04 领域实体（Domain Entities）

> 单元：U04 — 推广合作核心  
> 范围：Promotion 实体 + 4 Python Enum + 1 序列号表 + 1 领域事件  
> 不含：Settlement 实体（U05）、企微通知（U07）、采集 Worker（U13）

---

## 1. 实体清单

| # | 实体 | 类型 | 多租户 | 说明 |
|---|---|---|---|---|
| 1 | `Promotion` | TenantScopedModel | ✅ | 推广合作记录（业务核心表） |
| 2 | `PromotionSequence` | TenantScopedModel | ✅ | internal_code 序列号表（按 (tenant_id, date_key) 计数） |
| 3 | `PublishStatus` | Python Enum | — | 5 状态 |
| 4 | `RecallStatus` | Python Enum | — | 4 状态（含默认） |
| 5 | `SettlementStatus` | Python Enum | — | 5 状态 |
| 6 | `Platform` | Python Enum | — | 平台（与 U03 复用） |
| 7 | `ReviewAction` | Python Enum | — | 2 值（approve / reject） |

衍生字段（不持久化）：
- `dual_platform`：SQL 计算
- `urge_status`：SQL CTE 表达式 + Python service 双实现
- `effective_like_count`：service 层根据当前折算系数计算
- `is_hit`：service 层根据当前阈值计算
- `cpl`：service 层根据 `quote_amount / effective_like_count` 计算

领域事件：
- `SettlementRequested`：审核 approve 时发出，被 U05 监听
- `PromotionPublished`：publish 时发出，预留 U07 监听（U04 阶段无 listener）

---

## 2. ER 图（Mermaid）

```mermaid
erDiagram
    Tenant ||--o{ Promotion : owns
    Tenant ||--o{ PromotionSequence : owns
    Style ||--o{ Promotion : "style_id (U02)"
    Sku ||--o{ Promotion : "sku_id (U02 可选)"
    Blogger ||--o{ Promotion : "blogger_id (U03)"
    User ||--o{ Promotion : "pr_id 创建人"
    User ||--o{ Promotion : "reviewed_by 审核人"

    Promotion {
        UUID id PK
        UUID tenant_id FK
        UUID style_id FK
        UUID sku_id FK_optional
        UUID blogger_id FK
        UUID pr_id FK
        string internal_code "UNIQUE per tenant"
        string style_code_snapshot
        string style_short_name_snapshot
        decimal quote_amount "DECIMAL(10,2)"
        decimal cost_snapshot "DECIMAL(10,2)"
        string platform
        date cooperation_date
        date scheduled_publish_date
        date actual_publish_date
        string publish_url
        text cancel_reason
        text recall_reason
        int like_count
        string note_title
        text remark
        string publish_status "5 状态"
        string recall_status "4 状态"
        string settlement_status "5 状态"
        UUID reviewed_by
        timestamp reviewed_at
        string review_action
        text review_reason
        boolean is_active
        timestamp created_at
        timestamp updated_at
    }

    PromotionSequence {
        UUID id PK
        UUID tenant_id FK
        date date_key
        int last_seq "0..9999"
    }
```

---

## 3. Promotion 字段详细

### 3.1 关联字段

| 字段 | 类型 | 必填 | FK / 引用 | 说明 |
|---|---|---|---|---|
| `id` | UUID | ✅ | — | 主键 |
| `tenant_id` | UUID | ✅ | tenant.id | 继承 TenantScopedModel |
| `style_id` | UUID | ✅ | style.id ON DELETE RESTRICT | 必关联款式 |
| `sku_id` | UUID | ❌ | sku.id ON DELETE RESTRICT | 可选；填则单 SKU 推广 |
| `blogger_id` | UUID | ✅ | blogger.id ON DELETE RESTRICT | 必关联博主 |
| `pr_id` | UUID | ✅ | user.id ON DELETE SET NULL | 创建该 promotion 的 PR |

### 3.2 业务键

| 字段 | 类型 | 必填 | 唯一 | 说明 |
|---|---|---|---|---|
| `internal_code` | VARCHAR(64) | ✅ | (tenant_id, internal_code) UNIQUE | 格式 `<DE><yyMMdd><0001>`，按 cooperation_date 当天累加 |

### 3.3 快照字段（创建时一次性快照，不重算）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `style_code_snapshot` | VARCHAR(64) | ✅ | 创建时从 style.style_code 复制 |
| `style_short_name_snapshot` | VARCHAR(128) | ✅ | 创建时从 style.short_name 或 style_name 复制 |
| `quote_amount` | DECIMAL(10,2) | ✅ | 合作报价；创建时从 blogger.quote 快照，后续可手动调整 |
| `cost_snapshot` | DECIMAL(10,2) | ❌ | 总成本快照；创建时按 sku.cost_price 聚合（若有 sku_id），否则 NULL |

### 3.4 业务字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `platform` | VARCHAR(16) | ✅ | — | Platform 枚举 |
| `cooperation_date` | DATE | ✅ | — | 合作日期（影响 internal_code） |
| `scheduled_publish_date` | DATE | ❌ | NULL | 预定发布日期 |
| `actual_publish_date` | DATE | ❌ | NULL | publish 时填入 |
| `publish_url` | VARCHAR(512) | ❌ | NULL | publish 时必填（service 校验） |
| `cancel_reason` | TEXT | ❌ | NULL | cancel 时必填 |
| `recall_reason` | TEXT | ❌ | NULL | start_recall 时可填 |
| `like_count` | INTEGER | ❌ | NULL | 原始点赞量；CHECK ≥ 0 |
| `note_title` | VARCHAR(255) | ❌ | NULL | 笔记标题 |
| `remark` | TEXT | ❌ | NULL | 备注 |

### 3.5 三个状态字段

| 字段 | 类型 | 必填 | 默认 | 状态 |
|---|---|---|---|---|
| `publish_status` | VARCHAR(16) | ✅ | `'未发布'` | 未发布 / 已发布 / 已取消 / 异常 / 已删除 |
| `recall_status` | VARCHAR(16) | ✅ | `'未召回'` | 未召回 / 召回中 / 召回成功 / 召回失败 |
| `settlement_status` | VARCHAR(16) | ✅ | `'未核查'` | 未核查 / 待核查 / 待付款 / 已付款 / 已驳回 |

### 3.6 审核相关

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `reviewed_by` | UUID | ❌ | 审核人 user.id |
| `reviewed_at` | TIMESTAMPTZ | ❌ | 审核时间 |
| `review_action` | VARCHAR(16) | ❌ | ReviewAction 枚举（approve / reject） |
| `review_reason` | TEXT | ❌ | reject 时必填 |

### 3.7 通用字段

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `is_active` | BOOLEAN | `true` | 软停用（与 publish_status="已删除" 区分） |
| `created_at` | TIMESTAMPTZ | `now()` | 继承 TenantScopedModel |
| `updated_at` | TIMESTAMPTZ | `now()` | 继承 |

> 注：U04 不设 `is_deleted` 字段；删除走 `publish_status="已删除"` 状态机路径。

---

## 4. PromotionSequence 字段（internal_code 序列号表）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | — |
| `tenant_id` | UUID FK | — |
| `date_key` | DATE | 与 promotion.cooperation_date 同 |
| `last_seq` | INTEGER | 最后使用的序列号（0..9999） |

**唯一约束**：`UNIQUE (tenant_id, date_key)`

**生成流程**（service 层 + 行级锁）：
```python
# 在事务中
seq = SELECT * FROM promotion_sequence
        WHERE tenant_id=:t AND date_key=:d
        FOR UPDATE  -- 行级锁，避免并发
if seq is None:
    INSERT INTO promotion_sequence (tenant_id, date_key, last_seq) VALUES (..., 1)
    new_seq = 1
else:
    UPDATE promotion_sequence SET last_seq = last_seq + 1 WHERE id = seq.id
    new_seq = seq.last_seq + 1

internal_code = f"{tenant_prefix}{cooperation_date.strftime('%y%m%d')}{new_seq:04d}"
```

防 race condition：`SELECT FOR UPDATE` + 同事务 INSERT promotion 一并 commit。

---

## 5. 索引清单

### 5.1 promotion 表

| 索引 | 类型 | 列 / 表达式 | 用途 |
|---|---|---|---|
| `uq_promotion_internal_code` | B-tree UNIQUE | `(tenant_id, internal_code)` | 业务键唯一 |
| `idx_promotion_tenant_active` | B-tree | `(tenant_id, is_active, publish_status)` | 列表过滤 |
| `idx_promotion_pr` | B-tree | `(tenant_id, pr_id)` | 按 PR 筛选 |
| `idx_promotion_blogger` | B-tree | `(tenant_id, blogger_id, publish_status)` | 重复检测 + 按博主查 |
| `idx_promotion_style` | B-tree | `(tenant_id, style_id, publish_status)` | 重复检测 + dual_platform 计算 |
| `idx_promotion_cooperation_date` | B-tree | `(tenant_id, cooperation_date DESC)` | 默认排序 |
| `idx_promotion_scheduled_date` | B-tree | `(tenant_id, scheduled_publish_date)` | urge_status 计算 |
| `idx_promotion_settlement_status` | B-tree | `(tenant_id, settlement_status)` | 财务查询（U05 用） |
| `idx_promotion_recall_status` | B-tree | `(tenant_id, recall_status)` | 召回查询 |
| `idx_promotion_publish_dates` | B-tree | `(tenant_id, publish_status, scheduled_publish_date)` | urge_status SQL CTE |
| `idx_promotion_internal_code_trgm` | GIN trgm (partial) | `(internal_code gin_trgm_ops) WHERE is_active = true` | 关键字搜索 |
| `idx_promotion_style_code_snapshot_trgm` | GIN trgm (partial) | `(style_code_snapshot gin_trgm_ops) WHERE is_active = true` | 同 |
| `idx_promotion_short_name_trgm` | GIN trgm (partial) | `(style_short_name_snapshot gin_trgm_ops) WHERE is_active = true` | 同 |

### 5.2 promotion_sequence 表

| 索引 | 类型 | 列 |
|---|---|---|
| `uq_promotion_sequence` | B-tree UNIQUE | `(tenant_id, date_key)` |

---

## 6. 枚举定义

### 6.1 PublishStatus（5 状态）
```python
class PublishStatus(str, Enum):
    UNPUBLISHED = "未发布"
    PUBLISHED = "已发布"
    CANCELLED = "已取消"
    ABNORMAL = "异常"
    DELETED = "已删除"
```

### 6.2 RecallStatus（4 状态）
```python
class RecallStatus(str, Enum):
    NOT_RECALLED = "未召回"
    RECALLING = "召回中"
    RECALLED_SUCCESS = "召回成功"
    RECALLED_FAILURE = "召回失败"
```

### 6.3 SettlementStatus（5 状态）
```python
class SettlementStatus(str, Enum):
    NOT_REVIEWED = "未核查"
    PENDING_REVIEW = "待核查"
    PENDING_PAYMENT = "待付款"
    PAID = "已付款"
    REJECTED = "已驳回"
```

### 6.4 Platform（与 U03 一致，本单元复用 U03 enum）
```python
# 直接 from app.modules.blogger.enums import Platform
```

### 6.5 ReviewAction
```python
class ReviewAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
```

---

## 7. 领域事件

### 7.1 SettlementRequested（U05 监听 / 关键事件）

```python
@dataclass(frozen=True)
class SettlementRequested:
    """审核 approve 时发出，被 U05 SettlementService 监听并创建 settlement 记录。
    
    幂等保证：
    - U04 端：每次审核生成新 event_id（UUID4）
    - U05 端：DB UNIQUE(promotion_id) 兜底；service 层 SELECT 检查兜底
    """
    event_type: ClassVar[str] = "SettlementRequested"
    
    event_id: UUID                        # 幂等键（每次审核新生成）
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    style_id: UUID
    amount: Decimal                       # = promotion.quote_amount
    requested_by: UUID                    # = pr_manager.id（审核人）
    requested_at: datetime                # = promotion.reviewed_at
```

### 7.2 PromotionPublished（U07 预留 / 阶段无 listener）

```python
@dataclass(frozen=True)
class PromotionPublished:
    """publish 时发出。U04 阶段无 listener（U07 启用前消息丢弃）。"""
    event_type: ClassVar[str] = "PromotionPublished"
    
    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    promotion_id: UUID
    promotion_internal_code: str
    blogger_id: UUID
    publish_url: str
    publish_date: date
    pr_id: UUID
```

### 7.3 事件总线（MVP 阶段实现）

```python
# core/events.py（U04 新建，后续单元复用）
from collections.abc import Callable
from typing import Any

_handlers: dict[str, list[Callable]] = {}

def subscribe(event_type: str, handler: Callable) -> None:
    _handlers.setdefault(event_type, []).append(handler)

async def dispatch(event: Any) -> None:
    """同事务同步触发（MVP）。V1+ 评估升级 Celery / Redis Streams。"""
    for handler in _handlers.get(event.event_type, []):
        await handler(event)
```

U05 实施时通过 `subscribe("SettlementRequested", SettlementService.handle_settlement_requested)` 注册。

---

## 8. 与 U01/U02/U03 的关系

| 关系 | 来源 | 引用方式 |
|---|---|---|
| `tenant_id` | U01 | 继承 TenantScopedModel |
| `style_id` / `sku_id` | U02 | FK + 创建时快照 style_code / short_name |
| `blogger_id` | U03 | FK + 创建时快照 quote 到 quote_amount |
| `pr_id` / `reviewed_by` | U01 | FK 到 user.id |
| 状态机基类 | U01 `core/state_machine.py` | **首次实战使用**；本单元定义 3 个 transition table |
| 审计 | U01 | `@audit("promotion.create / publish / cancel / recall / review / ...")` |
| 权限装饰器 | U01 | `@require_permission("promotion:read/write/...")` |
| 字段权限模式 | U02/U03 | 复用模式：AMOUNT_VISIBLE_ROLES + AMOUNT_WRITABLE_ROLES |
| 审计敏感值脱敏 | U02/U03 | 复用 `*_changed: true` 标记策略 |
| upsert 模式 | U02/U03 | U04 不需要 upsert（promotion 创建即唯一） |

---

## 9. 演化路线图

| 阶段 | 单元 | 演化项 |
|---|---|---|
| **MVP** | U04（本单元） | 上述全部 |
| **MVP** | U05 | 监听 SettlementRequested 事件创建 settlement |
| **MVP** | U07 | 监听 PromotionPublished 事件发企微通知 |
| **MVP** | U08 | publish_progress.py 部分指标（已发布数 / 催发中数） |
| **V1** | U09 | 字段级权限改造：AMOUNT_VISIBLE_ROLES → Permission.field_filter() |
| **V1** | U10b | BloggerTagService 自动更新 blogger 标签（U04 端通过 promotion 历史协助质量评分） |
| **V1** | U13 | 数据采集 Worker 调用 `update_like_count` 内部 API |
| **V1** | U14 | 投产报表预聚合 promotion 汇总数据 |
| **V1+** | (system_setting 单元) | legacy_settings.py 改读动态配置（PLATFORM_LIKE_COEFFICIENT 等） |
| **V2** | U16 | promotion → order 映射（拍单刷单） |
| **V2** | U18 | AI 决策建议（基于 promotion 历史） |

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 继承 TenantScopedModel | ✅ |
| 强依赖关系明确（U01/U02/U03） | ✅ |
| 业务键 UNIQUE 设计支持序列号生成 | ✅ |
| 衍生字段不持久化（避免数据漂移） | ✅ |
| 快照字段持久化（避免历史变更） | ✅ |
| 3 个状态字段独立 + publish 主线约束 | ✅ |
| 领域事件 payload 完整含幂等键 | ✅ |
| 索引覆盖性能 SLA 路径 | ✅ |
| 与 U05 / U07 / U13 演化预留 | ✅ |
