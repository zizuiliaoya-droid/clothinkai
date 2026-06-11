# U06e NFR 设计计划（NFR Design Plan）

> 单元：U06e — 结算导入适配器
> 范围：1 个增量模式 P-U06e-01（INSERT-only + promotion 派生 + UNIQUE 冲突 catch + 合成 event_id + 不触发事件）；其余继承 U06a + U06d 思路 + U05 sequence/format_settlement_no
> 节奏：小增量（同 U06d 结构）

---

## 1. 与基线模式的关系

| 模式 | 来源 | U06e 用法 |
|---|---|---|
| P-U06a-01 Runner 事务 + 租户上下文（NF-1） | U06a | adapter per-row 事务内执行 |
| P-U06a-02 Adapter 协议 + Registry（NF-4） | U06a | SettlementImportAdapter + register() |
| P-U06d-01 INSERT-only + FK 解析 | U06d | 复用结构（FK 派生 + 序列 + INSERT） |
| U05 next_settlement_sequence + format_settlement_no | U05 | settlement_no 生成 |

### 1.1 U06e 唯一增量模式
- **P-U06e-01**：INSERT-only settlement（promotion 派生 + UNIQUE(promotion_id) 冲突 catch + 合成 request_event_id + 不触发事件）

---

## 2. 澄清问题（已预填，请审阅 [Answer] 标签）

### Q1 — adapter 内是否经 U05 service？
[Answer] **否，直接用 Repository**（同 U06b/c/d：U05 SettlementService 自带 commit/audit/**事件触发**/状态机校验，与 runner per-row 事务 FB-C 冲突 + 导入不应触发事件）。adapter 用传入 session 构造 PromotionRepository/SettlementRepository。

### Q2 — promotion 派生
[Answer] PromotionRepository.get_by_internal_code → promotion；blogger_id=promotion.blogger_id / style_id=promotion.style_id / pr_id=promotion.pr_id（派生，不让文件提供）。promotion None → RowValidationError。

### Q3 — UNIQUE(promotion_id) 冲突处理
[Answer] settlement add + flush；catch IntegrityError → RowValidationError("该推广已有结算单（不可重复，FB3）")。per-row 事务隔离（runner savepoint 回滚该行）。注意：IntegrityError 后该 session 需在 runner 层 rollback（runner 的 per-row `async with AsyncSessionApp()` 已处理 —— 异常冒泡触发 context manager 回滚）。

### Q4 — 合成 request_event_id
[Answer] `request_event_id = uuid4()`（每行唯一，满足 UNIQUE(request_event_id) + 标识导入来源）。

### Q5 — settlement_no 生成
[Answer] `next_settlement_sequence(tenant_id, settlement_date)` + `format_settlement_no(tenant_code, settlement_date, sequence)`；tenant_code 实例级缓存。

### Q6 — 不触发事件
[Answer] adapter 不调 event_bus.dispatch；直接 Repository.add（无 SettlementPaid 等）。

### Q7 — status / 金额字段组装
[Answer] settlement_status = parsed or "待核查"；amount/total_amount 必填（历史值）；payment_amount/payment_date/note_title/remark 可选；payment_proof_attachment_id=None；reviewed_by/paid_by/reviewed_at 留 None（历史迁移不还原审核人，记入文档简化）。

### Q8 — 测试模式
[Answer] 复用 U06a/b/c/d test_import_runner 模式。真实 SettlementImportAdapter；seed promotion（含 blogger/style）+ 已有 settlement（模拟事件创建）→ committed；event_capture 断言不触发事件；清理 settlement/settlement_sequence/seed。

---

## 3. 生成产物（2 份文档）

### 3.1 nfr-design-patterns.md
- **P-U06e-01：INSERT-only settlement 编排**
  - parse_row（_to_date/_to_decimal）+ validate（status 枚举）+ upsert（promotion 派生 → settlement_no + 合成 event_id → INSERT + IntegrityError catch）完整伪代码
  - 事务契约：复用 runner session 不 commit；不触发事件
  - UNIQUE(promotion_id) 冲突 catch → RowValidationError
  - 内置默认 vs field_mapping 双路
- 继承声明：P-U06a-01~05 + P-U06d-01 + U05 sequence/format
- 一致性校验

### 3.2 logical-components.md
- 新增组件 adapters/settlement.py（SettlementImportAdapter + _DEFAULT_COLUMNS + _to_date + _to_decimal + _get_tenant_code + register()）
- 复用 U04/U05 Repository + U06a 框架
- 注册序列（main.py 已预置）+ 依赖图 + 数据流 + 测试组件
- 无新表/端点/Celery 任务/main.py 改动

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06e/nfr-design/{nfr-design-patterns,logical-components}.md`

---

**等待用户回复"继续"批准本计划（含 8 个 [Answer]），开始生成 2 份 NFR 设计文档。**
