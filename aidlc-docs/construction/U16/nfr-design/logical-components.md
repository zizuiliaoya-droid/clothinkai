# U16 逻辑组件（拍单 / 刷单 / 余额）

> 单元：U16（EP06-S09、S10、S11）（V2）
> 复用 modules/finance，追加 6 新建 + 11 横切修改；无循环依赖。

---

## 1. 新建组件（modules/finance，6）

| 组件 | 类型 | 职责 | 依赖 |
|---|---|---|---|
| `order_adjustment_models.py` | ORM | OrderAdjustment + BalanceRecord（TenantScopedModel + RLS） | core/db |
| `order_adjustment_schemas.py` | Pydantic | BrushingCreate / OrderAdjustmentResponse / BalanceRecordCreate / BalanceRecordResponse | — |
| `order_adjustment_repository.py` | Repository | OrderAdjustmentRepository（add/get_by_promotion/list/exists_order_no）+ BalanceRecordRepository（add/last_balance/list） | models |
| `order_adjustment_service.py` | Service | auto_create_from_promotion（幂等）/ create_brushing（金额解析）/ list | repository, AuditService, parse_amount_expr |
| `balance_service.py` | Service | add_record（计算+校验）/ list | repository, AuditService |
| `order_adjustment_api.py` | Router | 拍单/刷单 + 余额 API | deps |

> parse_amount_expr 放 `order_adjustment_service.py` 顶部模块函数（或 finance/domain.py）。

---

## 2. 横切修改（11）

| 文件 | 改动 |
|---|---|
| `finance/enums.py` | +OrderType(拍单/刷单) / OrderAdjustmentStatus(待付款/已付款) / BalanceRecordType(充值/推广支出/刷拍单支出/其他) |
| `finance/exceptions.py` | +AmountExpressionInvalidError / BalanceMismatchError / BalanceTypeFieldMismatchError（均 422） |
| `finance/listeners.py` | +on_settlement_requested_auto_order；register() 追加 subscribe("SettlementRequested") |
| `finance/permissions.py` | +finance.order:read/write / finance.balance:read/write |
| `finance/deps.py` | +OrderAdjustmentServiceDep / BalanceServiceDep |
| `promotion/models.py` | +in_store_order Boolean NOT NULL DEFAULT false |
| `services/metric/style_roi.py` | 移除 exclude_brushing 占位 TODO（公式基于剔除后 pay） |
| `report/advanced_repository.py` | aggregate_by_style +exclude_brushing 参数（pay 减刷单 SUM 子查询） |
| `report/production_service.py` | get_report 默认 exclude_brushing=true |
| `report/advanced_api.py` | get_production query exclude_brushing 默认 true |
| `core/metrics.py` | +order_adjustment_auto_created_total{result} |
| `main.py` | 挂 order_adjustment_router |
| `tests/conftest.py` | 追加 finance.order_adjustment_models import + order_adjustment_factory（可选） |
| `alembic/versions/020_*.py` | 2 表 + promotion ALTER + scope seed |

> finance.listeners.register() 已被 main.register_event_listeners 第 1 步调用（U05）；U16 在同 register() 内追加 subscribe，无需改 main 的 listener 注册逻辑。

---

## 3. 依赖图（无循环）

```
order_adjustment_api → OrderAdjustmentService → OrderAdjustmentRepository → order_adjustment_models
                     → BalanceService → BalanceRecordRepository
                     → AuditService(U01)

finance.listeners.on_settlement_requested_auto_order (SettlementRequested 多 handler)
        → OrderAdjustmentService.auto_create_from_promotion
        → PromotionRepository(U04).get_by_id（读 in_store_order）

report.ProductionRepository.aggregate_by_style(exclude_brushing)
        → order_adjustment 子查询（内联 SQL，不跨模块调 finance repo）
report.ProductionService(U14, exclude_brushing=true) ← AnomalyAlertService(U15) / production API
```

依赖层级：U16 → U05（finance 模块/事件）→ U04 → U01；U16 → U14（report）→ U13/U05。无环（U04/U14 仅被读，不反向依赖 U16）。

---

## 4. migration 020 DDL 概要

```
revision = "020_u16_order_adjustment_balance"
down_revision = "019_u15_wecom_alert_tables"

order_adjustment（base + ）:
  order_type String(8) NOT NULL / order_date Date NULL / order_no String(64) NULL
  blogger_identifier String(128) NULL
  style_id UUID FK style RESTRICT NULL / sku_id UUID FK sku SET NULL NULL
  amount Numeric(12,2) NOT NULL / payment_amount Numeric(12,2) NULL
  payment_date Date NULL / payment_proof_attachment_id UUID FK attachment RESTRICT NULL
  exclude_from_roi Boolean NOT NULL DEFAULT false
  status String(8) NOT NULL DEFAULT '待付款'
  promotion_id UUID FK promotion SET NULL NULL / remark Text NULL
  UNIQUE(tenant_id, promotion_id) WHERE promotion_id IS NOT NULL  [uq_order_adjustment_promotion]
  INDEX(tenant_id, order_type, order_date) / INDEX(tenant_id, style_id, exclude_from_roi)
  CHECK amount >= 0 / order_type IN / status IN

balance_record（base + ）:
  record_date Date NOT NULL / record_type String(16) NOT NULL
  income Numeric(12,2) NULL / expense Numeric(12,2) NULL
  balance_after Numeric(12,2) NOT NULL / remark String(255) NULL
  created_by UUID FK user SET NULL NULL
  INDEX(tenant_id, created_at)
  CHECK income IS NULL OR income >= 0 / expense IS NULL OR expense >= 0

ALTER promotion ADD COLUMN in_store_order Boolean NOT NULL DEFAULT false

enable_rls_sql("order_adjustment"); enable_rls_sql("balance_record")
seed: finance.order:read/write + finance.balance:read/write（finance 显式 + admin 通配）
```

down：drop 2 表 + drop column in_store_order + 删 4 scope。

---

## 5. 启动序列影响

- `finance.listeners.register()`（U05 既有，main 第 1 步调用）内追加 subscribe("SettlementRequested", on_settlement_requested_auto_order)；与 U05 on_settlement_requested 同事件多 handler，dispatch 顺序执行。
- `main` 挂 order_adjustment_router（/api/finance 前缀）。
- 无新 Celery 任务 / Beat（U16 同步 + 事件驱动）。

---

## 6. 测试组件映射（3 文件）

| 测试文件 | 目标组件 | 用例要点 |
|---|---|---|
| `tests/unit/test_order_amount_balance.py` | parse_amount_expr + BalanceService._validate_type_field + 计算 | "100-30"→70 / 纯数字 / 多减号 422 / 负结果 422 / 充值仅 income / 支出仅 expense / 余额累加 |
| `tests/integration/test_order_adjustment.py` | listener + OrderAdjustmentService + BalanceService + ProductionService | 自动拍单（in_store_order true 创建 / 幂等二次跳过 / false 不创建）+ create_brushing exclude_from_roi + ROI 剔除（投产 pay 减刷单）+ 余额计算/expected 不一致 422/类型错配 422 + RLS |
| `tests/api/test_order_adjustment_api.py` | order_adjustment_api | brushing + balance-records 401 + OpenAPI 路径 |

- 复用 conftest fixtures：session/tenant_a/factory/finance_role/admin_role/product_factory/promotion_factory；ROI 测试需 platform_product + qianniu_daily（U13/U14 模式）+ order_adjustment 刷单行。

---

## 7. 一致性校验

- 与 nfr-design-patterns P-U16-01~04 伪代码组件一致。
- 与 functional-design domain-entities 组件清单（6 新建 + 11 横切）一致。
- 复用 U05 finance 事件多 handler、U14 ProductionRepository 聚合、U01 events/metrics/audit，无重复实现。
- 依赖图无循环（拓扑：U01 → U04 → U05 → U16；U13/U05 → U14 → U16）。
