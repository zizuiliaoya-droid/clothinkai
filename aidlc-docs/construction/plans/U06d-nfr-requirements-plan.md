# U06d NFR 需求计划（NFR Requirements Plan）

> 单元：U06d — 推广导入适配器
> 范围：U06d 特异性 NFR（INSERT-only + FK 解析 + 序列生成正确性）；框架级继承 U06a，通用继承 U01-U05
> 节奏：小增量（比 U06b/c 略多 —— FK 解析每行多往返 + 序列并发 + date 解析 + 幂等限制）

---

## 1. 与基线的关系

### 1.1 完全继承
- U06a 框架 NFR（异步吞吐 / upload P95 / 解析内存 / Celery 失败语义 / 行级隔离 FB-C / NF-1 SET LOCAL / 文件威胁 / CSV injection / 5 指标）
- U04 NFR（next_internal_sequence FB2 原子序列 / promotion GIN trgm / 字段权限过渡 AMOUNT_VISIBLE_ROLES）
- U02/U03 NFR（style/blogger 查询 + RLS）
- U01 通用 NFR

### 1.2 U06d 增量（4 项，比 U06b/c 多）
1. **解析正确性**：Decimal（quote/cost，禁 float）+ date（cooperation/scheduled）
2. **FK 解析正确性**：style/blogger 必需 + sku 可选；缺失 → 行失败（不静默建残缺 promotion）
3. **序列生成正确性 + 并发**：每行 next_internal_sequence（FB2 原子）；导入串行无 race，但行回滚可能跳号（可接受）
4. **INSERT-only 幂等限制**：跨文件重复无 dedup（已知限制，文档化）

---

## 2. 澄清问题（已预填，请审阅 [Answer] 标签）

### Q1 — adapter 每行 DB 往返
[Answer] **3-4 次**：① style 查（必需）；② blogger 查（必需）；③ sku 查（仅 sku_code 非空）；④ next_internal_sequence（1 条 INSERT ON CONFLICT）；⑤ promotion INSERT flush。约 4-5 次/行。比 U06b/c 多，但导入非高频，5 万行仍在 U06a SLA 内（~100-150 行/秒，5 万行 ≤ 6-8 分钟，略放宽于 U06a 5 分钟基线但可接受，记入文档）。V1 评估 style/blogger 批量预解析缓存。

### Q2 — 解析正确性
[Answer] Decimal（quote/cost，禁 float，去千分位）；date（cooperation 必需 + scheduled 可选，date.fromisoformat YYYY-MM-DD，非法 → 校验失败）；非法/负数 → import_job.failed。

### Q3 — FK 解析正确性
[Answer] style_code/xiaohongshu_id 必需，缺失 → 行失败（不建残缺 promotion）；sku_code 提供则必须有效。FK 查询受 RLS 约束（per-row SET LOCAL，仅本租户 style/blogger 可见 → 跨租户引用自动失败）。

### Q4 — 序列生成 + 并发
[Answer] 复用 U04 next_internal_sequence（FB2 单条 INSERT ON CONFLICT DO UPDATE RETURNING，原子）。导入单 batch 串行（U06a runner），同 cooperation_date 序号连续。行失败回滚 → 序号 UPDATE 同事务回滚（不浪费）；不同 batch 并发或行间回滚可能跳号（业务可接受，internal_code 唯一性靠 uq_promotion_internal_code partial UNIQUE）。

### Q5 — INSERT-only 幂等限制
[Answer] 文档化已知限制：跨文件相同推广 → 重复 promotion（与 U04 重复检测 warning 一致）。同文件 hash 409 + batch 内 UNIQUE(batch_id,row_number) 防同 batch 重复。V1 评估可选 dedup 键。

### Q6 — 容量
[Answer] 复用 IMPORT_MAX_ROWS=50000。5 万行 = 5 万 promotion INSERT + 5 万 sequence UPDATE（同 date 累加）。promotion_sequence 当天上限 9999（U04 CHECK）→ 单 cooperation_date 单 batch 超 9999 行会序列溢出失败（记入文档；实际导入按多日期分布则不触发）。

### Q7 — 可观测性
[Answer] 复用 U06a 5 指标（source=manual_promotion label）。无新增。adapter 不逐行打点。

### Q8 — 安全
[Answer] 复用 U06a 文件威胁 + csv_safe。金额字段（quote_amount/cost_snapshot）不回显 structlog（U04 AMOUNT_VISIBLE_ROLES，MVP 对 promotion:write 可见，U09 切字段级）。raw_data 保真。

### Q9 — 测试
[Answer] 真实 PromotionImportAdapter：unit（parse_row _to_date/_to_decimal + validate）+ integration（seed style+blogger → upload 样本 CSV → runner → promotion 入库 + internal_code 生成 + 序号连续 + 缺 style/blogger failed + partial + tenant_id）。adapter ≥ 85%。

### Q10 — 测试 DB 与异步
[Answer] 复用 U06a/b/c 模式：直接 await _run_import_batch（monkeypatch session + mock get_object_bytes）；committed 数据 + 清理（含 promotion + promotion_sequence + seed style/blogger）。

---

## 3. 生成产物（2 份文档）
- nfr-requirements.md：基线关系 + 4 项增量 + 性能（每行 4-5 往返，SLA 略放宽）+ 正确性（Decimal/date/FK/序列）+ INSERT-only 幂等限制 + 安全（金额日志不回显）+ 5 指标 + 测试
- tech-stack-decisions.md：复用 U04 sequence/format_internal_code + U02/U03 FK 查询 + U06a；唯一增量 adapters/promotion.py + _to_date；无新依赖/服务/配置/指标

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06d/nfr-requirements/{nfr-requirements,tech-stack-decisions}.md`

---

**等待用户回复"继续"批准本计划（含 10 个 [Answer]），开始生成 2 份 NFR 需求文档。**
