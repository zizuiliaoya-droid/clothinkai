# U06b NFR 设计计划（NFR Design Plan）

> 单元：U06b — 商品/SKU 导入适配器
> 范围：1 个增量设计模式（P-U06b-01：style 复用/创建 + sku upsert 在 runner per-row 事务内编排）；其余全部继承 U06a P-U06a-01~05 + U02 P-U02-03
> 节奏：极小增量（adapter 层无新基础设施模式；仅落地"一行 → 两实体"的事务内编排细节）

---

## 1. 与基线模式的关系

### 1.1 完全继承（不重复设计）

| 模式 | 来源 | U06b 用法 |
|---|---|---|
| P-U06a-01 导入 Runner 事务 + 租户上下文 | U06a | adapter 在 runner per-row 事务内被调用；runner 已设 SET LOCAL（NF-1）+ 成功/失败 job 写入 |
| P-U06a-02 ImportAdapter 协议 + Registry | U06a | StyleSkuImportAdapter 实现协议 + register() |
| P-U06a-03 DB 先行上传 + hash 去重 | U06a | upload(source=manual_style_sku) 走框架，adapter 无关 |
| P-U06a-04 两类失败重试 + 批次互斥 | U06a | 行级失败 → import_job.failed → retry only_failed |
| P-U06a-05 安全文件处理 | U06a | 解析/csv_safe/大小行数上限由框架处理 |
| P-U02-03 数据库原子 upsert | U02 | `SkuRepository.upsert_atomic`（ON CONFLICT RETURNING is_inserted） |

### 1.2 U06b 唯一增量模式
- **P-U06b-01**：单行"一个 SKU + 其 Style"的事务内 upsert 编排（style get-or-create + sku upsert，复用传入 session，不自 commit）

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — style get-or-create 在 adapter 内还是借 U02 service
[Answer] **adapter 内直接用 U02 Repository**（不经 U02 Service）：
- 理由：U02 `StyleService.create_style` / `SkuService.upsert_sku` 自带 `self._session.commit()` + audit + 字段权限检查 + Schema 校验，与 runner per-row 事务边界（FB-C：adapter 不 commit）冲突；且 worker 无 HTTP User/权限上下文
- adapter 持有 runner 传入的 session，构造 `StyleRepository(session)` / `SkuRepository(session)` 直接操作；commit 由 runner 控制
- 审计：导入的逐行 audit 不写（5 万行洪泛）；batch 级 audit 由 U06a service 写（import.upload / import.batch_completed）

### Q2 — style 复用查询方式
[Answer] `StyleRepository.get_by_code(style_code, include_deleted=False)`（U02 既有，走 uq_style_code partial UNIQUE，命中复用 id）。未命中 → `session.add(Style(...))` + `flush()` 拿 id（不调 code_exists 再查，flush 时 UNIQUE 冲突 → 该行 failed，retry 复用，Q4@nfr-req）。

### Q3 — sku upsert values 构造
[Answer] `SkuRepository.upsert_atomic(tenant_id, values)`，values 含：sku_code / style_id（来自 step1 style.id）/ color / size / cost_price / purchase_price / base_price / sourcing_type。INSERT-only 字段（id/tenant_id/created_at/style_id/sku_code/is_deleted）由 upsert_atomic 内部排除更新（U02 既有逻辑）。返回 (sku, is_inserted)。

### Q4 — brand 软关联实现
[Answer] adapter 内轻量查询：`select(Brand.id).where(Brand.tenant_id==tid, Brand.brand_code==code)`（brand_code 非空时）；查到填 style.brand_id，查不到 None（不报错）。仅在**创建 style** 时使用（复用既有 style 不改 brand）。MVP 不缓存（brand 数量少；同 batch 多行同 brand 的 N 次查询可接受，V1 评估 batch 内缓存）。

### Q5 — 行内多实体事务原子性
[Answer] style + sku 在**同一 per-row 事务**内（runner 的 `async with AsyncSessionApp()` + SET LOCAL）：style flush + sku upsert 要么同 commit 要么同 rollback。若 sku upsert 失败 → 整行 rollback（含新建的 style 一并回滚）→ import_job.failed。保证不产生"建了 style 但没 sku"的孤儿（与 P-U06a-01 per-row 原子一致）。

### Q6 — Decimal 解析落点
[Answer] **parse_row 内转换**（纯函数）：价格字段 `_parse_decimal`（去千分位 + Decimal，禁 float）；解析异常 → 在 parse_row 抛 ValueError 由 runner 记 failed，或在 validate 阶段统一校验（选 **validate 校验 + parse_row 尽力转换**：parse_row 转换失败保留原值字符串，validate 检测非 Decimal → 失败，统一错误文案 BR-U06b-13）。

### Q7 — 内置默认映射 vs field_mapping
[Answer] adapter 持有**模块级常量** `_DEFAULT_COLUMNS`（domain-entities §4 的 12 列）。parse_row(row, mapping)：mapping 非 None → 用 mapping.mapping_config["columns"]；mapping None → 用 `_DEFAULT_COLUMNS`。两路统一走同一映射执行函数。

### Q8 — 测试模式
[Answer] 复用 U06a test_import_runner 模式（monkeypatch AsyncSessionApp/Bypass → 测试 engine + mock get_object_bytes 注入样本 CSV + committed 数据 + finally 清理）。注册真实 StyleSkuImportAdapter（非 Fake）。断言 style/sku 真实入库 + Decimal 精度 + 复用既有 style + partial。

---

## 3. 生成产物（2 份文档）

### 3.1 nfr-design-patterns.md
- **P-U06b-01：单行两实体 upsert 编排**
  - 时序：parse_row（含 Decimal 转换）→ validate → upsert（StyleRepository get-or-create → SkuRepository.upsert_atomic）→ 返回 (sku.id, is_inserted)
  - 事务契约：复用 runner per-row session（不自 commit）；style+sku 同事务原子（Q5）
  - 伪代码（adapter.upsert 完整实现示意）
  - brand 软关联（仅建 style 时查，Q4）
  - 内置默认映射 vs field_mapping 双路（Q7）
- 继承声明：P-U06a-01~05 + P-U02-03 不重复，仅引用
- 一致性校验表（per-row 原子 / Decimal 禁 float / 复用 U02 ON CONFLICT / 不 commit）

### 3.2 logical-components.md
- 新增组件：`modules/importer/adapters/style_sku.py`（StyleSkuImportAdapter + _DEFAULT_COLUMNS + _parse_decimal + register()）
- 复用组件清单（U02 StyleRepository/SkuRepository/Brand；U06a runner/registry/api）
- 注册序列（复用 U06a register_import_adapters，main.py 已预置模块路径，无改动）
- 依赖图（adapter → U02 repo + U06a registry/runner）
- **无新表 / 无新端点 / 无新 Celery 任务 / 无 main.py·celery_app.py 改动**

---

## 4. 文件影响预估（NFR Design 阶段仅文档）
- `aidlc-docs/construction/U06b/nfr-design/nfr-design-patterns.md`
- `aidlc-docs/construction/U06b/nfr-design/logical-components.md`

---

**等待用户回复"继续"批准本计划（含预填的 8 个 [Answer]），开始生成 2 份 NFR 设计文档。**
