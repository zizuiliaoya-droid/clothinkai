# U06d NFR 设计计划（NFR Design Plan）

> 单元：U06d — 推广导入适配器
> 范围：1 个增量模式 P-U06d-01（INSERT-only + FK 解析 + 序列生成编排）；其余继承 U06a P-U06a-01~05 + U04 next_internal_sequence/format_internal_code
> 节奏：小增量（比 U06b/c 略复杂，但仍单模式）

---

## 1. 与基线模式的关系

| 模式 | 来源 | U06d 用法 |
|---|---|---|
| P-U06a-01 Runner 事务 + 租户上下文（NF-1） | U06a | adapter 在 per-row 事务内执行 |
| P-U06a-02 Adapter 协议 + Registry（NF-4） | U06a | PromotionImportAdapter + register() |
| P-U06a-03/04/05 上传/重试/安全 | U06a | 框架处理 |
| P-U06b-01 / P-U06c-01 单行 upsert 编排 | U06b/c | **改为 INSERT-only + 多 FK 解析**（不同写入语义） |
| U04 next_internal_sequence（FB2 原子）+ format_internal_code | U04 | internal_code 生成 |

### 1.1 U06d 唯一增量模式
- **P-U06d-01**：INSERT-only promotion 编排（FK 解析 style/blogger/sku + internal_code 序列生成 + 快照 + 3 状态默认）

---

## 2. 澄清问题（已预填，请审阅 [Answer] 标签）

### Q1 — adapter 内是否经 U04 service？
[Answer] **否，直接用 Repository**（同 U06b/c：U04 PromotionService.create_promotion 自带 commit/audit/重复检测 warning/字段权限，与 runner per-row 事务边界 FB-C 冲突，worker 无 HTTP User）。adapter 用 runner 传入 session 构造 StyleRepository/BloggerRepository/SkuRepository/PromotionRepository。

### Q2 — FK 解析顺序与失败
[Answer] upsert 内顺序：① style（必需，未找到 raise）；② blogger（必需，未找到 raise）；③ sku（sku_code 非空时，未找到 raise）。raise 的异常被 runner 捕获 → import_job.failed（error_detail）。FK 查询用传入 session（RLS 约束本租户）。

### Q3 — internal_code 生成落点
[Answer] FK 解析成功后：① `_get_tenant_code(session, tenant_id)`（实例级缓存 dict[UUID,str]，tenant.code 不可变）；② `next_internal_sequence(tenant_id, cooperation_date)`（U04 FB2 原子）；③ `format_internal_code(tenant_code, cooperation_date, sequence)`。SequenceOverflowError → raise → 行失败。

### Q4 — promotion 字段组装
[Answer] Promotion(style_id, sku_id, blogger_id, pr_id=actor_id, internal_code, style_code_snapshot=style.style_code, style_short_name_snapshot=style.short_name or style.style_name, quote_amount, cost_snapshot, platform or "小红书", cooperation_date, scheduled_publish_date, note_title, remark, publish_status/recall_status/settlement_status=DB server_default 初始态)。add + flush 拿 id。tenant_id 由 ORM 钩子注入。

### Q5 — 3 状态初始态
[Answer] **不显式设置**（依赖 U04 ORM server_default：未发布/未召回/未核查）。或显式传初始态值确保一致。选**不显式传**（让 server_default 生效，与 U04 create_promotion 一致；flush 后若需读状态则 refresh —— 但 adapter 只返回 id，无需读状态）。

### Q6 — tenant_code 缓存
[Answer] adapter 实例级 `_tenant_code_cache: dict[UUID, str]`；首次查 `select(Tenant.code).where(Tenant.id==tid)`，缓存。tenant.code 不可变 → 缓存安全（worker 进程长生命周期，跨 batch 复用）。

### Q7 — date / Decimal 解析落点
[Answer] parse_row 内 `_to_date`（date.fromisoformat）+ `_to_decimal`（禁 float）；非法保留原串；validate 检测非 date/非 Decimal/负数/必填空。

### Q8 — 测试模式
[Answer] 复用 U06a/b/c test_import_runner 模式（monkeypatch session + mock get_object_bytes + committed 清理）。真实 PromotionImportAdapter；测试需 seed style+blogger（committed）+ 清理 promotion/promotion_sequence。

---

## 3. 生成产物（2 份文档）

### 3.1 nfr-design-patterns.md
- **P-U06d-01：INSERT-only promotion 编排**
  - parse_row（_to_date/_to_decimal）+ validate + upsert（FK 解析 → tenant_code 缓存 → sequence → format_internal_code → INSERT）完整伪代码
  - 事务契约：复用 runner session 不 commit；FK+sequence+INSERT 同 per-row 事务原子
  - FK 解析失败 raise → runner failed
  - 内置默认 vs field_mapping 双路
- 继承声明：P-U06a-01~05 + U04 sequence/format
- 一致性校验

### 3.2 logical-components.md
- 新增组件 adapters/promotion.py（PromotionImportAdapter + _DEFAULT_COLUMNS + _to_date + _to_decimal + _get_tenant_code 缓存 + register()）
- 复用 U02/U03/U04 Repository + U06a 框架
- 注册序列（main.py 已预置）+ 依赖图 + 数据流 + 测试组件
- 无新表/端点/Celery 任务/main.py 改动

## 4. 文件影响（仅文档）
- `aidlc-docs/construction/U06d/nfr-design/{nfr-design-patterns,logical-components}.md`

---

**等待用户回复"继续"批准本计划（含 8 个 [Answer]），开始生成 2 份 NFR 设计文档。**
