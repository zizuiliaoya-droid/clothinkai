# U06d 功能设计计划（Functional Design Plan）

> 单元：U06d — 推广导入适配器
> 阶段：MVP 第 9 个 sub-unit（导入并行支线，第 3 个业务 Adapter）
> 依赖：**U04（promotion 表 + PromotionRepository + next_internal_sequence + format_internal_code）+ U02（style/sku 查询）+ U03（blogger 查询）+ U06a（框架）**
> 节奏：单批生成（**比 U06b/c 复杂** —— INSERT-only + 多 FK 解析 + internal_code 生成 + 快照 + 3 状态）

---

## 0. 单元定位与关键复杂度

U06d 是 **U06a 框架第 3 个业务 Adapter**，但与 U06b/c 有本质差异：

| 维度 | U06b/c（upsert 型） | U06d（INSERT-only 型） |
|---|---|---|
| 业务键 | sku_code / xiaohongshu_id（文件提供） | **internal_code 系统生成**（序列号，文件不提供） |
| 写入语义 | upsert_atomic（ON CONFLICT） | **INSERT-only**（每行建新 promotion） |
| FK 解析 | 0-1（style/brand 软关联） | **2 必需（style_code→style_id + xiaohongshu_id→blogger_id）+ 1 可选（sku_code→sku_id）** |
| 额外生成 | 无 | **internal_code（next_internal_sequence + format_internal_code，需 tenant_code）** |
| 快照 | 无 | style_code_snapshot / style_short_name_snapshot |
| 状态 | 无 | 3 状态默认（未发布/未召回/未核查） |

### 0.1 上游既有能力（复用）

| 能力 | 来源 | U06d 用法 |
|---|---|---|
| ImportAdapter 协议 / Registry / runner / 8 端点 | U06a | 实现 + register manual_promotion |
| `PromotionRepository.add` / `next_internal_sequence`（FB2 原子序列） / `get_by_internal_code` | U04 | 建 promotion + 生成 internal_code |
| `format_internal_code(tenant_code, cooperation_date, sequence)` | U04 domain | internal_code 格式化 |
| `StyleRepository.get_by_code` | U02 | style_code → style_id |
| `BloggerRepository.get_by_xiaohongshu_id` | U03 | xiaohongshu_id → blogger_id |
| `_to_decimal` 思路 | U06b/c | quote_amount / cost_snapshot |
| register_import_adapters（main.py 已含 adapters.promotion 路径） | U06a | 自动注册 |

---

## 1. 覆盖故事
EP07-S07~S10（共享）；额外验收 = 推广目标表 FK 解析 + internal_code 生成 + 端到端样本 CSV。

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — source 标识
[Answer] **`manual_promotion`**（与 main.py 预置 `adapters.promotion` 路径一致）。target_table=`promotion`。

### Q2 — 写入语义（关键决策：INSERT-only）
[Answer] **INSERT-only**（每行建一个新 promotion，internal_code 系统生成）。理由：
- internal_code 由 `next_internal_sequence` + `format_internal_code` 系统生成，文件不提供，**无法 upsert by internal_code**
- promotion 无其他天然唯一业务键（U04 的"重复检测"是 warning 非约束，不适合做导入幂等键）
- 不引入新 UNIQUE 约束（adapter 不改 U04 schema / 无 migration）
- **幂等保证**：① U06a 文件 hash 去重（同文件 → 409，框架层）；② U06a `UNIQUE(batch_id, row_number)`（同 batch 同行不重复处理）；③ only_failed 重试仅重跑**失败行**（成功行已建 promotion，不再处理）；④ 解析失败整文件重试时尚无行处理（无重复）
- **已知限制**：两个不同文件含相同逻辑推广 → 创建重复 promotion（与 U04 重复检测为 warning 的语义一致，不在导入层强约束）。记入文档，V1 评估可选 force/dedup 键。
- is_inserted 恒为 True。

### Q3 — FK 解析（style / blogger / sku）
[Answer]
- **style_code → style_id**（必需）：`StyleRepository.get_by_code`；未找到 → 行失败（`款式编码 X 不存在`）
- **xiaohongshu_id → blogger_id**（必需）：`BloggerRepository.get_by_xiaohongshu_id`；未找到 → 行失败（`博主 X 不存在`）
- **sku_code → sku_id**（可选）：提供时 `SkuRepository.get_by_code`（U02 既有），未找到 → 行失败（提供了就必须有效）；不提供 → None
- FK 解析在 upsert 阶段（需 DB）；validate 仅校验必填字段非空（纯函数不碰 DB）

### Q4 — internal_code 生成
[Answer] upsert 内：① 查 tenant.code（**adapter 实例级缓存 keyed by tenant_id**，tenant.code 不可变，安全）；② `next_internal_sequence(tenant_id, cooperation_date)`（FB2 原子）；③ `format_internal_code(tenant_code, cooperation_date, sequence)`。SequenceOverflowError（>9999）→ 行失败。

### Q5 — 快照字段
[Answer] 从解析到的 style 取：`style_code_snapshot = style.style_code`；`style_short_name_snapshot = style.short_name or style.style_name`。`quote_amount` 从文件行（必需，≥0）；`cost_snapshot` 从文件行（可选）或 None（MVP 不自动从 sku 算，避免额外查询 + 与 U04 create 行为差异；记为简化）。

### Q6 — 3 个状态字段
[Answer] **全部默认初始态**：publish_status=未发布 / recall_status=未召回 / settlement_status=未核查。**不从文件导入状态**（导入 = 创建待处理推广；状态由 U04 正常 API 驱动，避免状态机一致性校验进 adapter）。已发布历史推广导入留 V1（需 actual_publish_date + 状态校验）。

### Q7 — 字段映射（manual_promotion v1 默认列）
[Answer]

| source_col | target_field | required | type | 说明 |
|---|---|---|---|---|
| 款式编码 | style_code | ✅ | str | → style_id（FK 解析） |
| SKU编码 | sku_code | — | str | → sku_id（可选 FK） |
| 小红书ID | xiaohongshu_id | ✅ | str | → blogger_id（FK 解析） |
| 报价金额 | quote_amount | ✅ | decimal | ≥0 |
| 成本快照 | cost_snapshot | — | decimal | ≥0，可空 |
| 平台 | platform | ✅ | str | 默认"小红书" |
| 合作日期 | cooperation_date | ✅ | date | YYYY-MM-DD（internal_code + 业务） |
| 计划发布日期 | scheduled_publish_date | — | date | |
| 笔记标题 | note_title | — | str | |
| 备注 | remark | — | str | |

### Q8 — date 类型解析（U06d 新增）
[Answer] cooperation_date 必需 + scheduled_publish_date 可选：`date.fromisoformat`（YYYY-MM-DD）；非法 → 行失败。新增 `_to_date` 纯函数。cooperation_date 是 internal_code 前缀依据（必需有效）。

### Q9 — pr_id
[Answer] `pr_id = actor_id`（batch.created_by，上传者即录入 PR；与 U04 create_promotion 的 pr_id=user.id 一致）。

### Q10 — 行校验（validate 纯函数）
[Answer] 必填非空：style_code / xiaohongshu_id / quote_amount / platform / cooperation_date。quote_amount/cost_snapshot 非空时 Decimal ≥0。cooperation_date/scheduled_publish_date 非空时合法 date。长度上限（对齐 U04）。FK 存在性不在 validate（需 DB，放 upsert）。

### Q11 — 事务（FB-C）
[Answer] upsert 不自 commit；runner per-row 事务 + SET LOCAL（NF-1）。FK 解析 + sequence + insert 同 per-row 事务原子（任一失败整行回滚，含已占用的 sequence —— 注：sequence 是独立 ON CONFLICT INSERT，回滚会释放该序号？**否**，next_internal_sequence 在同一 session 事务内，行回滚则序号 UPDATE 也回滚，不浪费号；但并发下序号可能跳号，可接受）。

### Q12 — 权限 / 注册 / 测试
[Answer] 复用 U06a importer.batch:read/write + importer.mapping:write。注册复用 register_import_adapters（main.py 已含 adapters.promotion）。测试：真实 PromotionImportAdapter（unit parse_row/validate/_to_date + integration 端到端 FK 解析 + internal_code 生成 + 缺 style/blogger 失败 + partial）。

---

## 3. 生成产物（3 份功能设计文档）
- domain-entities.md：PromotionImportAdapter 契约 + manual_promotion 10 列映射 + _to_date + FK 解析 + internal_code 生成 + INSERT-only 语义图
- business-rules.md：BR-U06d-01~：标识/INSERT-only/FK 解析失败/internal_code/快照/状态默认/date 解析/校验/事务/幂等限制/框架边界
- business-logic-model.md：UC（注册/端到端导入含 FK 解析+序列+建 promotion/行级失败/自定义映射/幂等语义）+ 端到端样本 CSV

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06d/functional-design/{domain-entities,business-rules,business-logic-model}.md`

---

**等待用户回复"继续"批准本计划（含 12 个 [Answer]，注意 Q2 INSERT-only 决策与幂等限制），开始生成 3 份功能设计文档。**
