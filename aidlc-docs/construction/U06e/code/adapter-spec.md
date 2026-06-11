# U06e 适配器规格（SettlementImportAdapter）

> 文件：`backend/app/modules/importer/adapters/settlement.py`
> 协议：实现 U06a `ImportAdapter`（`source` / `target_table` / `parse_row` / `validate` / `upsert`）+ 模块级 `register()`

---

## 1. 类属性

| 属性 | 值 |
|---|---|
| `source` | `manual_settlement` |
| `target_table` | `settlement` |

`__init__`：初始化 `_tenant_code_cache: dict[UUID, str]`（tenant.code 不可变，worker 跨 batch 复用）。

---

## 2. 模块级常量与工具

| 名称 | 说明 |
|---|---|
| `_VALID_STATUS` | `frozenset(s.value for s in SettlementStatus)` — 5 枚举值 |
| `_DEFAULT_COLUMNS` | 9 列默认映射（mapping=None 回退） |
| `_REQUIRED` | `(("promotion_internal_code", "推广编号"),)` |
| `_to_date(raw)` | `date.fromisoformat`；空→None；非法→原串（供 validate 捕获） |
| `_to_decimal(raw)` | 去千分位 + `Decimal`（禁 float）；空→None；非法→原串 |

---

## 3. 方法规格

### parse_row(row, mapping) → dict
- mapping 非 None：用 `mapping.mapping_config["columns"]`；否则 `_DEFAULT_COLUMNS`。
- 按 `type` 分发：`decimal` → `_to_decimal`；`date` → `_to_date`；其余 → strip 或 None。
- 纯函数，无 DB / Session 依赖。

### validate(parsed) → list[str]
返回错误描述列表（空=通过）。校验项：
1. `promotion_internal_code` 必填。
2. `amount` / `total_amount` 必填且为 `Decimal ≥ 0`。
3. `payment_amount` 可选；提供时须 `Decimal ≥ 0`。
4. `settlement_date` 必填且为 `date`（非法原串 → "格式错误"）。
5. `payment_date` 可选；提供时须为 `date`。
6. `settlement_status` 可选；提供时须 ∈ `_VALID_STATUS`。
7. `note_title` ≤ 255。

FK（promotion）存在性**不在 validate**，在 upsert 阶段查。

### async upsert(parsed, *, session, tenant_id, actor_id) → (UUID, bool)
1. **promotion 派生**：`PromotionRepository.get_by_internal_code` → None 则
   `RowValidationError("推广编号 X 不存在")`。
2. **settlement_no**：`_get_tenant_code`（缓存）+ `next_settlement_sequence`
   （`SequenceOverflowError` 冒泡 → runner failed）+ `format_settlement_no`。
3. **INSERT**：构造 `Settlement`（`blogger_id`/`style_id`/`pr_id` 从 promo 派生；
   `settlement_status` 默认 "待核查"；`request_event_id = uuid4()`），`add` + `flush`。
4. **冲突**：`flush` 抛 `IntegrityError`（UNIQUE(tenant_id, promotion_id)）
   → `RowValidationError("该推广已有结算单（不可重复，FB3）")`。
5. 返回 `(settlement.id, True)`（INSERT-only）。
6. **不 commit**（runner 持有 per-row 事务）；**不 dispatch 事件**。

### async _get_tenant_code(session, tenant_id) → str
实例级缓存；miss → `SELECT tenant.code WHERE id=tenant_id`，None→`""`。

### register()
`ImportAdapterRegistry.register(SettlementImportAdapter())`（双进程 NF-4，由
`register_import_adapters` 调用；main.py 已预置模块路径）。

---

## 4. 依赖导入

| 来源 | 符号 |
|---|---|
| `app.modules.auth.models` | `Tenant` |
| `app.modules.finance.domain` | `format_settlement_no` |
| `app.modules.finance.enums` | `SettlementStatus` |
| `app.modules.finance.models` | `Settlement` |
| `app.modules.finance.repository` | `SettlementRepository` |
| `app.modules.promotion.repository` | `PromotionRepository` |
| `app.modules.importer.exceptions` | `RowValidationError` |
| `app.modules.importer.registry` | `ImportAdapterRegistry` |
| `sqlalchemy.exc` | `IntegrityError` |

---

## 5. 数据库往返（每行 3 次）
1. `get_by_internal_code`（SELECT promotion）
2. `next_settlement_sequence`（INSERT ON CONFLICT DO UPDATE RETURNING）
3. `flush`（INSERT settlement）

（`_get_tenant_code` 首行 1 次 SELECT，后续命中缓存。）
