# U16 领域实体（拍单 / 刷单 / 余额）

> 单元：U16（EP06-S09、S10、S11）（V2）
> 模块归属：复用 `modules/finance`，追加 2 表 + promotion.in_store_order ALTER + ROI 隔离接入
> 依赖：U05（finance/settlement + SettlementRequested 事件）、U14（ProductionService）

---

## 1. 实体总览

| 实体 | 表名 | 用途 | 关键约束 |
|---|---|---|---|
| OrderAdjustment | `order_adjustment` | 拍单 / 刷单统一建模 | UNIQUE(tenant_id, promotion_id) partial（自动拍单幂等） |
| BalanceRecord | `balance_record` | 余额流水（充值 / 支出 + 自动余额） | 无业务键（追加流水） |
| Promotion（U04 扩展） | `promotion` | +in_store_order 店铺拍单标志 | ALTER ADD COLUMN |

两新表继承 `TenantScopedModel`（U01）+ RLS。

---

## 2. OrderAdjustment（拍单 / 刷单统一建模）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | base | TenantScopedModel + RLS | |
| order_type | String(8) | NOT NULL | 拍单 / 刷单（OrderType 枚举） |
| order_date | Date | NULL | 订单日期 |
| order_no | String(64) | NULL | 订单号（刷单录入；重复=warning 不硬拒） |
| blogger_identifier | String(128) | NULL | 博主ID / 微信ID（拍单自动从 promotion/blogger 填） |
| style_id | UUID FK style RESTRICT | NULL | 款式 |
| sku_id | UUID FK sku SET NULL | NULL | SKU |
| amount | Numeric(12,2) | NOT NULL | 金额（刷单支持"原价-返现"解析后值） |
| payment_amount | Numeric(12,2) | NULL | 付款金额 |
| payment_date | Date | NULL | 付款日期 |
| payment_proof_attachment_id | UUID FK attachment RESTRICT | NULL | 付款截图 |
| exclude_from_roi | Boolean | NOT NULL DEFAULT false | 是否从 ROI 剔除（刷单默认 true） |
| status | String(8) | NOT NULL DEFAULT '待付款' | 待付款 / 已付款 |
| promotion_id | UUID FK promotion SET NULL | NULL | 自动拍单来源 promotion |
| remark | Text | NULL | 备注 |

索引：
- `uq_order_adjustment_promotion` UNIQUE(tenant_id, promotion_id) **partial WHERE promotion_id IS NOT NULL**（自动拍单幂等：一 promotion 至多一自动拍单）
- `idx_order_adjustment_type` (tenant_id, order_type, order_date)
- `idx_order_adjustment_style` (tenant_id, style_id)
- `idx_order_adjustment_roi` (tenant_id, style_id, exclude_from_roi)（ROI 隔离聚合）

CHECK：`amount >= 0`；`order_type IN ('拍单','刷单')`；`status IN ('待付款','已付款')`。

### 设计要点
- 拍单/刷单同表，`order_type` 区分（开发文档统一建模）。
- 刷单 `exclude_from_roi` 默认 true；拍单默认 false。
- `order_no` 不设 UNIQUE（开发文档：重复仅 warning 要求确认）；自动拍单用 promotion_id partial UNIQUE 幂等。

---

## 3. BalanceRecord（余额流水）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / tenant_id / created_at / updated_at | base | TenantScopedModel + RLS | |
| record_date | Date | NOT NULL | 流水日期 |
| record_type | String(16) | NOT NULL | 充值 / 推广支出 / 刷拍单支出 / 其他（BalanceRecordType） |
| income | Numeric(12,2) | NULL | 收入（充值类填） |
| expense | Numeric(12,2) | NULL | 支出（支出类填） |
| balance_after | Numeric(12,2) | NOT NULL | 本笔后余额（自动计算落库） |
| remark | String(255) | NULL | 备注 |
| created_by | UUID FK user SET NULL | NULL | 录入人 |

索引：`idx_balance_record_tenant_created` (tenant_id, created_at)。
CHECK：`income IS NULL OR income >= 0`；`expense IS NULL OR expense >= 0`。

### 设计要点
- `balance_after = 上一笔 balance_after + COALESCE(income,0) - COALESCE(expense,0)`，首笔上一笔余额视为 0。
- "上一笔" = 同租户 ORDER BY created_at DESC LIMIT 1（显式 WHERE tenant_id）。
- 类型字段匹配：充值仅 income / 推广支出·刷拍单支出仅 expense（错配 422）。
- payload 可携 `expected_balance`，与计算值不一致 → 422（开发文档"标红报错不允许保存"）。
- 流水追加（V2 仅新增 + list，不改不删）。

---

## 4. Promotion 扩展（U04）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| in_store_order | Boolean | NOT NULL DEFAULT false | 店铺拍单=是 → 审核通过自动生成拍单 |

ALTER promotion ADD COLUMN（不锁表，无回填）。

---

## 5. 枚举

| 枚举 | 值 |
|---|---|
| OrderType | 拍单 / 刷单 |
| OrderAdjustmentStatus | 待付款 / 已付款 |
| BalanceRecordType | 充值 / 推广支出 / 刷拍单支出 / 其他 |

---

## 6. ROI 隔离口径（EP06-S10 接入 U14 投产报表）

- 启用 `services/metric/style_roi` 的 `exclude_brushing`（移除占位 TODO）。
- `ProductionRepository.aggregate_by_style(exclude_brushing: bool)`：为 true 时
  `pay_amount` 减去该款式期内 `order_adjustment(order_type='刷单' AND exclude_from_roi=true)` 的 `SUM(amount)`（按 style_id + order_date BETWEEN）。
- `ProductionService.get_report` 默认 `exclude_brushing=true`（V2 起真实 ROI）；API query 默认 true。
- 无刷单数据时减 0，结果与 V1 一致（U14 测试不破坏）。

---

## 7. 组件清单（新建 / 修改）

### 新建（modules/finance）
| 文件 | 职责 |
|---|---|
| `order_adjustment_models.py` | OrderAdjustment + BalanceRecord ORM |
| `order_adjustment_schemas.py` | BrushingCreate / OrderAdjustmentResponse / BalanceRecordCreate / Response |
| `order_adjustment_repository.py` | OrderAdjustment + BalanceRecord 仓储（last_balance / roi 聚合辅助） |
| `order_adjustment_service.py` | auto_create_from_promotion / create_brushing / list |
| `balance_service.py` | add_record（自动计算 + 校验）/ list |
| `order_adjustment_api.py` | 拍单/刷单 + 余额 API |

### 修改（横切）
| 文件 | 改动 |
|---|---|
| `finance/enums.py` | +OrderType / OrderAdjustmentStatus / BalanceRecordType |
| `finance/listeners.py` | +on_settlement_requested_auto_order（拍单自动生成 best-effort） |
| `finance/permissions.py` | +finance.order:read/write / finance.balance:read/write |
| `finance/deps.py` | +OrderAdjustmentServiceDep / BalanceServiceDep |
| `promotion/models.py` | +in_store_order |
| `services/metric/style_roi.py` | 启用 exclude_brushing |
| `report/advanced_repository.py` | aggregate_by_style +exclude_brushing 剔除刷单 |
| `report/production_service.py` | 默认 exclude_brushing=true |
| `report/advanced_api.py` | get_production query 默认 true |
| `main.py` | 挂 order_adjustment_router + 注册 finance auto-order listener |
| `alembic/versions/020_*.py` | 2 表 + promotion ALTER + scope seed |

---

## 8. ER 关系

```
promotion(U04, +in_store_order) 1──0..1 order_adjustment（自动拍单 UNIQUE promotion_id）
style(U02) 1──* order_adjustment
sku(U02)   0..1──* order_adjustment
attachment 0..1──* order_adjustment（付款截图）
tenant 1──* balance_record（流水链 balance_after）

SettlementRequested(U04 审核通过) ──listener──> OrderAdjustmentService.auto_create_from_promotion
order_adjustment(刷单) ──exclude──> ProductionRepository(U14 投产 ROI)
```

---

## 9. 演化说明
- order_adjustment 付款流 V2 基础口径（status 2 态 + mark_paid 简化）；完整付款审批可后续增强。
- balance_record V2 仅新增 + list；冲正/修改留后续。
- exclude_brushing 默认 true 后，U14 投产报表口径升级为"真实 ROI"。
