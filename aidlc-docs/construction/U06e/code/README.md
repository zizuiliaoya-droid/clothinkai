# U06e 代码生成说明（结算导入适配器）

> 单元：U06e — 结算导入适配器（导入支线最后一个 Adapter）
> 节奏：单批生成（唯一代码增量 = `adapters/settlement.py` + 2 测试文件）
> 依赖：U04（promotion `get_by_internal_code`）+ U05（`SettlementRepository` + `next_settlement_sequence` + `format_settlement_no` + `SettlementStatus`）+ U06a（统一导入框架）

---

## 1. 交付物清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `backend/app/modules/importer/adapters/settlement.py` | 新建 | SettlementImportAdapter（INSERT-only 历史迁移） |
| `backend/tests/unit/test_settlement_adapter.py` | 新建 | parse_row + validate 纯函数单测（24 例） |
| `backend/tests/integration/test_import_settlement.py` | 新建 | 端到端（adapter→runner→入库，2 例） |

**未改动**：`main.py`（已预置 `adapters.settlement` 注册路径）/ `celery_app.py` / migration / api / 权限 / runner。

---

## 2. 核心语义（settlement 是特例）

结算单由 U04 事件（`SettlementRequested`）创建，FB3 财务记录**永久不可替换**（无 `is_active`/软删除），
`UNIQUE(tenant_id, promotion_id)` 一个推广一辈子只能有一条结算单。因此 U06e 适配器用于
**历史/遗留结算数据迁移**（INSERT-only），不是日常运营导入。

| 特性 | 实现 |
|---|---|
| INSERT-only | `add` + `flush`；`is_inserted` 恒 True |
| promotion 派生 | 文件仅提供推广编号 → `get_by_internal_code` → `blogger_id/style_id/pr_id` 从 promotion 派生 |
| settlement_no | `next_settlement_sequence`（FB2 原子序列）+ `format_settlement_no`（tenant_code 实例级缓存） |
| 合成 request_event_id | `uuid4()`（导入无真实事件，满足 `UNIQUE(request_event_id)`） |
| UNIQUE(promotion_id) 冲突 | catch `IntegrityError` → `RowValidationError("该推广已有结算单（不可重复，FB3）")` |
| 不触发事件 | 不调 `event_bus.dispatch` |
| 不经 U05 Service | 直接用 Repository（避免 commit/audit/事件/状态机校验，FB-C） |

---

## 3. 9 列字段映射（domain-entities §4）

| 中文表头 | target_field | 类型 | 必填 |
|---|---|---|---|
| 推广编号 | promotion_internal_code | str | ✅ |
| 结算日期 | settlement_date | date | ✅ |
| 金额 | amount | decimal ≥0 | ✅ |
| 总金额 | total_amount | decimal ≥0 | ✅ |
| 付款金额 | payment_amount | decimal ≥0 | 可选 |
| 付款日期 | payment_date | date | 可选 |
| 结算状态 | settlement_status | str（5 枚举，默认 待核查） | 可选 |
| 笔记标题 | note_title | str(≤255) | 可选 |
| 备注 | remark | str | 可选 |

`settlement_status` ∈ {待核查, 待付款, 待财务付款, 已付款, 已驳回}（`_VALID_STATUS`）。
`reviewed_by` / `paid_by` 导入时留 None。

---

## 4. 设计守护落地（P-U06e-01）

- **FB-C 不自 commit**：upsert 仅用 runner 传入 session。
- **NF-1 per-row SET LOCAL**：复用 U06a runner（`SELECT set_config('app.tenant_id', :tid, true)`）。
- **per-row 事务隔离**：runner 每行独立 `AsyncSessionApp()`，flush 失败仅毒化该行 session，
  context manager 回滚，外层 except 用独立 bypass session 写 `import_job.failed`。
- **date/Decimal 禁 float**：`_to_date` / `_to_decimal`（去千分位）。

---

## 5. 验证

- `getDiagnostics`：3 文件均无警告。
- Build & Test：见 `test-coverage.md`。
