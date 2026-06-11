# U06e NFR 需求计划（NFR Requirements Plan）

> 单元：U06e — 结算导入适配器
> 范围：U06e 特异性 NFR（历史迁移 INSERT-only + promotion 派生 + 不触发事件 + UNIQUE 一对一）；框架级继承 U06a，通用继承 U01-U05
> 节奏：小增量（同 U06d 结构 + 语义敏感性）

---

## 1. 与基线的关系

### 1.1 完全继承
- U06a 框架 NFR（异步吞吐 / upload P95 / 解析内存 / Celery 失败语义 / 行级隔离 FB-C / NF-1 SET LOCAL / 文件威胁 / CSV injection / 5 指标）
- U05 NFR（next_settlement_sequence FB2 原子 / settlement GIN trgm / FB3 永久不可替换 / 金额字段权限 PAYMENT_VISIBLE_ROLES）
- U04 NFR（promotion 查询 + RLS）
- U01 通用 NFR

### 1.2 U06e 增量（4 项）
1. 解析正确性：Decimal（amount/total/payment 禁 float）+ date（settlement/payment）+ status 枚举
2. promotion FK 派生正确性：blogger/style/pr 从 promotion 派生（不让文件提供）
3. **UNIQUE(promotion_id) 一对一幂等**：重复 promotion → IntegrityError → 行失败（FB3 不覆盖）
4. **不触发事件 + 不经 Service**：导入是数据迁移，正确性 = 不产生副作用事件

---

## 2. 澄清问题（已预填，请审阅 [Answer] 标签）

### Q1 — adapter 每行 DB 往返
[Answer] **3 次**：① promotion 查（get_by_internal_code）；② next_settlement_sequence（INSERT ON CONFLICT）；③ settlement INSERT flush。比 U06d（4-5）略少（promotion 派生省去 blogger/style 独立查）。5 万行历史迁移 ≤ 6-8 分钟（一次性运维操作，SLA 宽松）。

### Q2 — 解析正确性
[Answer] Decimal（amount/total/payment 禁 float 去千分位）；date（settlement_date 必需 + payment_date 可选）；settlement_status ∈ 5 枚举校验。非法 → 行失败。

### Q3 — promotion FK 派生正确性
[Answer] promotion_internal_code → promotion；blogger_id/style_id/pr_id 从 promotion 派生（保证与 promotion 一致，不让文件提供避免不一致）；promotion 未找到 → 行失败。promotion 查询受 RLS（仅本租户）。

### Q4 — UNIQUE(promotion_id) 一对一幂等
[Answer] settlement 已存在该 promotion → INSERT flush IntegrityError → catch 转 RowValidationError（行失败，FB3 不覆盖）。同文件 hash 409（U06a）+ batch 内 UNIQUE(batch_id,row_number)。**跨文件相同 promotion 也会被 UNIQUE(promotion_id) 拦截**（区别 U06d 无此约束）→ U06e 幂等性更强（DB 约束保证一对一）。

### Q5 — 不触发事件正确性
[Answer] adapter 不调 event_bus.dispatch；不经 U05 SettlementService（Service 含事件/audit/状态机）。NFR 测试：导入 settlement 后不产生 SettlementPaid 等事件（event_capture 断言空）。

### Q6 — 容量
[Answer] 复用 IMPORT_MAX_ROWS=50000。settlement_sequence 当天上限 9999（U05 CHECK）：单 settlement_date 单 batch >9999 行 → 序列溢出失败（记入文档；历史迁移按多日期分布不触发）。

### Q7 — 可观测性
[Answer] 复用 U06a 5 指标（source=manual_settlement label）。无新增。

### Q8 — 安全
[Answer] 复用 U06a 文件威胁 + csv_safe。金额字段（amount/total/payment_amount）不回显 structlog（U05 PAYMENT_VISIBLE_ROLES，MVP 对 importer.batch:write 可见，U09 切字段级）。raw_data 保真。

### Q9 — 测试
[Answer] 真实 SettlementImportAdapter：unit（parse_row _to_date/_to_decimal + validate status 枚举）+ integration（seed promotion + 已有 settlement → 导入 + settlement_no + 重复 promotion failed + 缺 promotion failed + 不触发事件 + partial）。adapter ≥ 85%。

### Q10 — 测试 DB 与异步
[Answer] 复用 U06a/b/c/d 模式：直接 await _run_import_batch（monkeypatch session + mock get_object_bytes）；committed 数据 + 清理（含 settlement + settlement_sequence + seed promotion/blogger/style）。

---

## 3. 生成产物（2 份文档）
- nfr-requirements.md：基线关系 + 4 项增量 + 性能（每行 3 往返）+ 正确性（Decimal/date/status/派生/UNIQUE/不触发事件）+ 安全（金额日志不回显）+ 5 指标 + 测试
- tech-stack-decisions.md：复用 U05 sequence/format_settlement_no + U04 promotion 查询 + U06a；唯一增量 adapters/settlement.py + _to_date + tenant_code 缓存；无新依赖/服务/配置/指标

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06e/nfr-requirements/{nfr-requirements,tech-stack-decisions}.md`

---

**等待用户回复"继续"批准本计划（含 10 个 [Answer]），开始生成 2 份 NFR 需求文档。**
