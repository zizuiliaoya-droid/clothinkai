# U06e 功能设计计划（Functional Design Plan）

> 单元：U06e — 结算导入适配器
> 阶段：MVP 第 10 个 sub-unit（导入并行支线，最后一个业务 Adapter）
> 依赖：**U05（settlement 表 + SettlementRepository + next_settlement_sequence + format_settlement_no）+ U04（promotion 查询）+ U06a（框架）**
> 节奏：单批生成（与 U06d 同构 —— INSERT-only + FK 解析 + 序列生成；**额外语义敏感性需先厘清**）

---

## 0. 关键语义厘清（U06e 特殊性）

U05 settlement 与 U06b/c/d 的目标表有本质不同：

| 特性 | 含义 | 对导入的影响 |
|---|---|---|
| **事件创建** | settlement 正常由 U04 review approve → SettlementRequested 事件 → finance listener 创建 | 导入是**非正常流程** |
| **FB3 永久不可替换** | 财务记录无 is_active，无 soft delete，service 无 delete | 导入只能 **INSERT**，绝不 update 既有 |
| **UNIQUE(promotion_id)** | 一个 promotion 一辈子只有一条 settlement | 同 promotion 重复导入 → 冲突失败 |
| **UNIQUE(request_event_id)** | 事件重放兜底 | 导入需生成**合成 request_event_id（uuid4）** |

### 0.1 U06e 的合理语义：历史结算数据迁移（Legacy Migration）

U06e **唯一合理用途 = 历史/遗留结算数据迁移**：新租户上线时，把其遗留系统的已有结算记录批量导入，关联到已迁移的 promotion。**不是**日常运营流程（日常走 U04 事件链路）。

- INSERT-only（settlement_no 系统生成 + 合成 request_event_id）
- 按 promotion internal_code 解析 → 派生 blogger_id/style_id/pr_id（保证与 promotion 一致，不让文件提供避免不一致）
- UNIQUE(promotion_id) → 该 promotion 已有 settlement（无论来自事件或导入）→ 行失败（不覆盖，FB3）
- settlement_status 从文件导入（历史数据可能是任意状态；validate 仅校验 ∈ 5 枚举，**不强制 per-status 字段完整性** —— 历史数据可信，区别于 live 状态机）
- **不触发任何事件**（导入是数据迁移，非业务动作；避免重复触发 SettlementPaid 等）

---

## 1. 与 U06d 的异同

| 维度 | U06d（推广） | U06e（结算） |
|---|---|---|
| 写入 | INSERT-only | INSERT-only（相同） |
| 业务键生成 | internal_code（序列） | settlement_no（序列，format_settlement_no） |
| FK 解析 | style_code + xhs_id（2 必需）+ sku | **promotion internal_code（1 必需，派生 blogger/style/pr）** |
| 合成字段 | 无 | **request_event_id = uuid4()**（导入无真实事件） |
| 唯一冲突 | uq_promotion_internal_code（partial）| **uq_settlement_promotion（永久，一对一）→ 重复 promotion 失败** |
| 状态 | 3 状态默认初始态 | **从文件导入（∈ 5 枚举）默认待核查** |
| 复用 | next_internal_sequence | next_settlement_sequence + format_settlement_no |

---

## 2. 覆盖故事
EP07-S07~S10（共享）；额外验收 = 结算目标表（历史迁移语义）FK 解析 + settlement_no 生成 + 端到端样本 CSV。

---

## 3. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — source 标识
[Answer] **`manual_settlement`**（与 main.py 预置 `adapters.settlement` 路径一致）。target_table=`settlement`。

### Q2 — 导入语义（关键决策）
[Answer] **历史结算数据迁移（INSERT-only）**。新租户遗留结算批量入库；不触发事件；FB3 不覆盖既有。日常结算仍走 U04 事件链路。文档明确标注 U06e 为运维迁移工具，非日常运营路径。

### Q3 — promotion FK 解析 + 字段派生
[Answer] 文件提供 **promotion internal_code** → `PromotionRepository.get_by_internal_code` → 解析 promotion；**blogger_id/style_id/pr_id 从 promotion 派生**（不让文件提供，保证一致性）。promotion 未找到 → 行失败 `推广编号 X 不存在`。

### Q4 — UNIQUE(promotion_id) 冲突
[Answer] settlement 已存在该 promotion（事件创建 or 之前导入）→ INSERT flush 抛 IntegrityError → 行失败 `该推广已有结算单（不可重复，FB3）`。adapter 内 catch IntegrityError 转 RowValidationError（per-row 事务隔离）。

### Q5 — settlement_no 生成
[Answer] `next_settlement_sequence(tenant_id, date_key)` + `format_settlement_no(tenant_code, date_key, sequence)`。date_key = 文件"结算日期"列（或缺省用 promotion.cooperation_date / today —— 选**文件提供 settlement_date 必填**，作为 settlement_no 日期段 + 业务记录）。tenant_code 实例级缓存（同 U06d）。

### Q6 — request_event_id 合成
[Answer] 导入无真实事件 → `request_event_id = uuid4()`（每行唯一，满足 UNIQUE 约束 + 标识"导入来源"）。文档注明该 UUID 为合成值（非真实 SettlementRequested 事件）。

### Q7 — settlement_status 导入
[Answer] 文件提供 settlement_status（可空，默认"待核查"）；validate 校验 **∈ 5 枚举值**（待核查/待付款/待财务付款/已付款/已驳回）；**不强制 per-status 字段完整性**（历史数据可信，区别 live 状态机）。导入终态结算（如已付款）允许，payment_amount/payment_date 从文件可选导入。

### Q8 — 金额字段
[Answer] amount（必填 ≥0）+ total_amount（必填 ≥0，历史值，不重算）+ payment_amount（可选 ≥0）。Numeric(12,2)。payment_date 可选 date。**不导入 payment_proof_attachment_id**（附件迁移不在导入范围，留 None；导入历史已付款记录的截图属 V1）。

### Q9 — 字段映射（manual_settlement v1 默认列）
[Answer]

| source_col | target_field | required | type | 说明 |
|---|---|---|---|---|
| 推广编号 | promotion_internal_code | ✅ | str | → promotion（派生 blogger/style/pr） |
| 结算日期 | settlement_date | ✅ | date | settlement_no 日期段 + 业务 |
| 金额 | amount | ✅ | decimal | ≥0 |
| 总金额 | total_amount | ✅ | decimal | ≥0（历史值，不重算） |
| 付款金额 | payment_amount | — | decimal | ≥0 |
| 付款日期 | payment_date | — | date | |
| 结算状态 | settlement_status | — | str | ∈ 5 枚举，默认待核查 |
| 笔记标题 | note_title | — | str | |
| 备注 | remark | — | str | |

### Q10 — 行校验（validate 纯函数）
[Answer] 必填：promotion_internal_code / settlement_date / amount / total_amount。amount/total_amount/payment_amount 非空时 Decimal ≥0。settlement_date 必填合法 date；payment_date 可选合法 date。settlement_status 非空时 ∈ 5 枚举。长度上限（settlement_no 由系统生成不校验；note_title≤255）。FK + UNIQUE 冲突在 upsert。

### Q11 — 事务（FB-C）+ 不触发事件
[Answer] upsert 不自 commit；runner per-row 事务 + SET LOCAL（NF-1）。FK 解析 + sequence + INSERT 同 per-row 事务原子。**不调 event_bus.dispatch**（导入是数据迁移，不触发 SettlementPaid 等；与 U05 service 的事件触发解耦）。不经 U05 SettlementService（避免 commit/audit/事件/状态机校验）。

### Q12 — 权限 / 注册 / 测试
[Answer] 复用 U06a importer.batch:read/write + importer.mapping:write。注册复用 register_import_adapters（main.py 已含 adapters.settlement）。测试：真实 SettlementImportAdapter（unit parse_row/validate/_to_date/状态枚举 + integration seed promotion → 导入 settlement + settlement_no 生成 + 重复 promotion 失败 + 缺 promotion 失败 + partial）。

---

## 4. 生成产物（3 份功能设计文档）
- domain-entities.md：SettlementImportAdapter 契约 + manual_settlement 9 列映射 + promotion FK 派生 + 合成 request_event_id + settlement_no 生成 + INSERT-only（历史迁移语义）
- business-rules.md：BR-U06e-01~：标识/历史迁移语义/INSERT-only/promotion FK 派生/UNIQUE 冲突/settlement_no/合成 event_id/状态导入/金额/不触发事件/不经 Service/校验/事务/框架边界
- business-logic-model.md：UC（注册/端到端历史迁移/重复 promotion 失败/自定义映射/状态导入）+ 端到端样本 CSV

## 5. 文件影响（仅文档）
- `aidlc-docs/construction/U06e/functional-design/{domain-entities,business-rules,business-logic-model}.md`

---

**等待用户回复"继续"批准本计划（含 12 个 [Answer]，注意 Q2 历史迁移语义 + Q4 一对一冲突 + Q6 合成 event_id + Q11 不触发事件），开始生成 3 份功能设计文档。**
