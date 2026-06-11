# U14 领域实体（Domain Entities）

> 单元：U14 — 工作进度 / 爆款约篇 / 店铺数据 / 投产报表
> 故事：EP09-S02~S05

---

## 1. 实体概览

| 实体 | 类型 | 用途 |
|---|---|---|
| `TargetPlanning` | ORM (TenantScopedModel) | 爆款约篇目标（PR×款式×月） |
| `StoreDaily` | ORM (TenantScopedModel) | 店铺日报手动输入字段（按 date） |

> 工作进度表 + 投产报表为**纯读聚合**（无新表）；读模型为 Pydantic schema。

---

## 2. TargetPlanning 实体（EP09-S03）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | — | TenantScopedModel | |
| pr_id | UUID | FK user ON DELETE RESTRICT | 负责 PR |
| style_id | UUID | FK style ON DELETE CASCADE | 款式 |
| period_month | VARCHAR(7) | NOT NULL | 'YYYY-MM' |
| min_target | INTEGER | NOT NULL, CHECK ≥0 | 最低约篇目标 |

约束：`UNIQUE(tenant_id, pr_id, style_id, period_month)`、`idx_target_planning_month(tenant_id, period_month)`、RLS。

---

## 3. StoreDaily 实体（EP09-S04）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | — | TenantScopedModel | |
| date | DATE | NOT NULL | 数据日期 |
| ad_spend_total | NUMERIC(12,2) | NULL | 全站推消耗（手动） |
| zhitongche_spend | NUMERIC(12,2) | NULL | 直通车消耗（手动） |
| yinli_spend | NUMERIC(12,2) | NULL | 引力魔方消耗（手动） |
| remark | TEXT | NULL | 备注 |

约束：`UNIQUE(tenant_id, date)`、RLS。

> 店铺看板 = qianniu_daily 按 date 聚合（自动）+ store_daily 手动字段（左联）。

---

## 4. 读模型 Schemas

### 4.1 工作进度（EP09-S02）

```python
class PrWorkProgress(BaseModel):
    pr_id: UUID | None
    pr_name: str
    quote_count: int            # 约篇件数
    in_schedule_count: int      # 档期内
    urge_count: int             # 催发
    important_urge_count: int   # 重要催发
    overdue_count: int          # 超时
    publish_count: int          # 已发布
    info_complete_rate: Decimal | None  # 信息完整度 = 已填点赞数/已发布数
    cancel_count: int           # 已取消
    recall_due_count: int       # 应召回
    recall_success_count: int   # 召回成功
    recall_complete_rate: Decimal | None  # 召回完成率
    overdue_rate: Decimal | None
    month_complete_rate: Decimal | None   # 月度完成率 = 已发布/约篇
    hit_count: int              # 爆文数（≥爆文统计阈值 500）
    hit_rate: Decimal | None    # 爆文率 = 爆文数/已发布
    like_count: int             # 点赞数（折算）
    cost: Decimal               # 成本
    cpl: Decimal | None         # 成本/有效点赞
```

### 4.2 爆款约篇（EP09-S03）

```python
class TargetCreate(BaseModel):
    pr_id: UUID
    style_id: UUID
    period_month: str  # YYYY-MM
    min_target: int

class TargetWithActual(BaseModel):
    pr_id: UUID
    pr_name: str
    style_id: UUID
    style_code: str
    style_name: str
    period_month: str
    min_target: int
    actual_count: int           # 实际约篇（聚合 promotion）
    status: str                 # 达标 / 未达标
    gap: int                    # actual - min（正=超额, 负=缺口）
```

### 4.3 店铺数据（EP09-S04）

```python
class StoreDailyRow(BaseModel):
    date: date
    visitors: int               # SUM(qianniu_daily.visitors)
    pay_amount: Decimal         # SUM(pay_amount)
    pay_orders: int             # SUM(pay_orders)
    ad_spend_total: Decimal | None      # 手动
    zhitongche_spend: Decimal | None    # 手动
    yinli_spend: Decimal | None         # 手动
    # 其他 extra JSONB 聚合字段按需

class StoreDailyManualUpdate(BaseModel):
    ad_spend_total: Decimal | None = None
    zhitongche_spend: Decimal | None = None
    yinli_spend: Decimal | None = None
    remark: str | None = None
```

### 4.4 投产报表（EP09-S05）

```python
class ProductionRow(BaseModel):
    style_id: UUID
    style_code: str
    style_name: str
    pay_amount: Decimal              # 支付金额
    refund_amount: Decimal           # 成功退款金额
    return_rate: Decimal | None      # 退货退款率 = 退款/支付
    confirmed_amount: Decimal        # 待确认收货金额 = 支付-退款
    promo_cost: Decimal              # 站外推广成本
    ad_spend: Decimal                # 站内投放
    total_spend: Decimal             # 推广总花费 = 站外+站内
    add_cart_count: int              # 总加购数
    add_cart_cost: Decimal | None    # 加购成本 = 总花费/加购数
    net_roi: Decimal | None          # 净投产比 = 待确认收货/总花费
    unit_deal_cost: Decimal | None   # 推广单件成交成本

class ProductionReport(BaseModel):
    items: list[ProductionRow]
    previous: list[ProductionRow] | None  # 上期（周环比）
    # change 由前端按 current vs previous 计算或服务端汇总
```

---

## 5. 聚合来源与时间维度

| 报表 | 来源表 | 时间字段 |
|---|---|---|
| 工作进度 | promotion | cooperation_date（月） |
| 爆款约篇 | target_planning + promotion | cooperation_date（月） |
| 店铺数据 | qianniu_daily + store_daily | qianniu_daily.date |
| 投产报表 | qianniu_daily + ad_daily + promotion + settlement | qianniu_daily.date |

---

## 6. 阈值常量

| 常量 | 默认 | 用途 |
|---|---|---|
| HIT_STAT_THRESHOLD | 500 | 工作进度表爆文统计阈值（≠ 爆文标记 1000） |
| 点赞折算系数 | 抖音/快手 ×0.1 | 复用 U04 PLATFORM_LIKE_COEFFICIENT |

> HIT_STAT_THRESHOLD 写 services/metric/work_progress 常量（V1+ system_setting 可配）。

---

## 7. 一致性校验

| 校验 | 结果 |
|---|---|
| EP09-S02 工作进度 KPI 全字段 | ✅ §4.1 |
| EP09-S03 target_planning + 达标 | ✅ §2 §4.2 |
| EP09-S04 store_daily 聚合+手动 | ✅ §3 §4.3 |
| EP09-S05 投产 5 公式 + 周环比 | ✅ §4.4 |
| 时间维度与开发文档契约一致 | ✅ §5 |
| 复用 U04/U05/U13 来源表 | ✅ §5 |
