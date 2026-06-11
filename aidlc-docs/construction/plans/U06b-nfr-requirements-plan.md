# U06b NFR 需求计划（NFR Requirements Plan）

> 单元：U06b — 商品/SKU 导入适配器
> 范围：U06b 特异性 NFR（adapter 解析/校验/upsert 正确性与性能）；框架级 NFR 全部继承 U06a，通用 NFR 继承 U01-U05
> 节奏：**极小增量**（无新表/新端点/新 Celery 任务/新依赖/新指标）；本计划仅列 U06b 新增维度

---

## 1. 与 U06a / U01-U05 NFR 基线的关系

### 1.1 完全继承（不重复定义）
- **U06a 全部框架 NFR**：异步导入吞吐（5 万行 ≤ 5 分钟）/ upload 同步段 P95 ≤ 2s / 文件解析内存 O(1) / Celery 失败语义 4 类 / 行级隔离（per-row 事务 + bypass 兜底）/ worker 租户上下文（SET LOCAL NF-1）/ 文件上传威胁模型 / CSV injection / 5 个导入指标
- **U02 全部 NFR**：style/sku 模糊搜索 GIN trgm / SkuRepository.upsert_atomic 原子性（P-U02-03）/ 字段权限过渡（PRICE_VISIBLE_ROLES，MVP 不强制字段级）
- **U01 通用 NFR**：多租户 RLS 双引擎 / 审计 / Prometheus / Sentry / structlog / pytest 框架

### 1.2 U06b 增量 NFR 维度（仅 4 项）
- **解析正确性**：Decimal 类型转换（去千分位 + 精度保真，不用 float）+ 空值/必填语义
- **upsert 幂等正确性**：重跑同 batch / 重复 sku_code 不产生重复 sku（依赖 U02 ON CONFLICT，需 U06b 集成验证）+ style 复用不误覆盖
- **adapter 性能贡献**：每行 upsert 的 DB 往返次数（style 查 + sku upsert）控制在常数，不拖累 U06a 5 万行 SLA
- **跨租户正确性**：adapter 在 runner per-row SET LOCAL 下创建的 style/sku 必带正确 tenant_id（RLS 验证，复用 U06a NF-1 守护）

### 1.3 与 U06a 关键差异

| 维度 | U06a（框架） | U06b（适配器） |
|---|---|---|
| 测试 adapter | FakeImportAdapter（内存，验证编排） | **真实 StyleSkuImportAdapter**（验证 style/sku 入库 + Decimal） |
| upsert 目标 | 无（委托 adapter） | U02 style（复用/建）+ sku（ON CONFLICT） |
| 数值处理 | 无 | **Decimal 精度**（cost/purchase/base price，不用 float） |
| 幂等保证 | UNIQUE(batch_id,row_number) | + **sku 业务键 ON CONFLICT**（U02 P-U02-03） |

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — adapter 每行 DB 往返预算
[Answer] 每行 ≤ **2 次 DB 往返**：① style 查（get_by_code）或建（INSERT flush）；② sku upsert_atomic（1 条 ON CONFLICT）。brand 查询仅 brand_code 非空时 +1（可缓存优化留 V1）。保证不拖累 U06a 5 万行 ≤ 5 分钟 SLA（~150-200 行/秒）。不在 MVP 引入批量 upsert（行级独立事务语义优先，FB-C）。

### Q2 — Decimal 解析精度
[Answer] 价格字段（cost_price/purchase_price/base_price）用 **`decimal.Decimal`**（不用 float，避免精度丢失）；去千分位逗号后解析；保留输入精度（U02 Sku 列为 Numeric(10,2)，DB 层兜底 2 位）。非法数值（无法解析 / 负数）→ 行校验失败（不静默置 0/None）。空字符串 → None。

### Q3 — upsert 幂等正确性 SLA
[Answer] **同一文件重复 upload** → U06a 框架层 409（hash 去重，不到 adapter）。**同一 batch retry / 文件内重复 sku_code** → adapter 依赖 U02 `ON CONFLICT(tenant_id, sku_code) WHERE is_deleted=false DO UPDATE` 幂等（第二次为 UPDATE 路径，is_inserted=False）。验证：重跑 only_failed 不新建重复 sku；文件内同 sku_code 两行 → 第二行 UPDATE（不报错）。

### Q4 — style 复用并发正确性
[Answer] 行级串行处理（U06a runner 单 batch 内串行，Q4@U06a），同 batch 内同 style_code 多行：第一行建 style，后续行 get_by_code 命中复用。**不同 batch 并发**同 style_code：依赖 U02 `uq_style_code` partial UNIQUE（INSERT 冲突 → 该行 failed，可 retry 时复用）。MVP 接受罕见并发建同 style 的 failed（retry 即复用），不引入 advisory lock。

### Q5 — 跨租户正确性
[Answer] 复用 U06a NF-1：runner per-row `SET LOCAL app.tenant_id` + ORM before_flush 注入 tenant_id。adapter 创建 Style 不显式写 tenant_id（钩子注入）；upsert_atomic 显式传 tenant_id（与会话 SET LOCAL 一致）。**NFR 测试**：runner 创建的 style/sku.tenant_id == batch.tenant_id（复用 U06a test_import_tenant_isolation 模式，本单元用真实 adapter 再验一次）。

### Q6 — 大文件 SKU 导入容量
[Answer] 复用 U06a `IMPORT_MAX_ROWS=50000`。5 万行商品导入 = 最多 5 万 sku upsert + ≤5 万 style 查/建（多数复用，实际 style 数远小于行数）。import_job 表每 batch ≤ 5 万行（U06a 容量基线）。无 U06b 特有容量增量。

### Q7 — 可观测性
[Answer] **复用 U06a 5 个指标**（import_batch_total / import_rows_total / import_batch_duration_seconds / import_file_size_bytes / import_retry_total），label `source="manual_style_sku"`自动区分。**不新增指标**。structlog 复用 U06a（batch_id/source/tenant_id）；adapter 内不额外打点（避免 5 万行日志洪泛）。

### Q8 — 安全
[Answer] 复用 U06a 文件上传威胁模型（白名单/上限/CSV injection/路径隔离）。U06b 增量：**raw_data 保真存储不转换**（失败下载时由 U06a csv_safe 防注入）；价格字段不回显到日志（结合 U02 价格字段 MVP 阶段对有 product:write 权限角色可见，U09 后切字段级）。无 U06b 特有安全增量。

### Q9 — 测试策略与覆盖
[Answer] 真实 adapter 测试（U06a 用 Fake，U06b 用真实 StyleSkuImportAdapter）：
- unit：parse_row（Decimal 千分位/空值/各类型）+ validate（必填/数值/白名单/长度各失败分支）— 纯函数无 DB
- integration：注册 adapter → upload 样本 CSV → run_import_batch → 断言 style/sku 入库 + 复用既有 style + Decimal 精度 + partial（缺字段失败行）+ retry only_failed 幂等
- 覆盖率：adapter ≥ 85%（继承 U01 service 基线）
- 复用 U06a 测试基建（FakeImportAdapter 不用，改注册真实 adapter；样本 CSV fixture）

### Q10 — 测试 DB 与异步调用
[Answer] 复用 U06a 模式：integration 测试**直接 await `_run_import_batch`**（不经 Celery broker），monkeypatch AsyncSessionApp/Bypass 指向测试 engine + mock get_object_bytes（FB-A，注入样本 CSV bytes）；committed 数据 + finally 清理（仿 U06a test_import_runner）。

---

## 3. 生成产物（2 份文档）

### 3.1 nfr-requirements.md
- 与 U06a/U02/U01 基线关系（完全继承 + 4 项增量）
- 性能（每行 ≤2 DB 往返 + 不拖累 5 万行 SLA + Decimal 解析无额外开销）
- 正确性（Decimal 精度 / upsert 幂等 / style 复用不覆盖 / 跨租户 tenant_id）
- 可靠性（行级失败语义继承 U06a；adapter 异常 → import_job.failed）
- 安全（raw_data 保真 + 复用 U06a 威胁模型；价格字段日志不回显）
- 可观测性（复用 U06a 5 指标，无新增）
- 测试覆盖（真实 adapter unit + integration + 端到端样本 CSV）
- 故事 NFR 映射（EP07-S07~S10 的 U06b 特有验收）

### 3.2 tech-stack-decisions.md
- 解析：复用 U06a openpyxl/csv（无新依赖）
- 数值：`decimal.Decimal`（不用 float）；去千分位
- upsert：复用 U02 SkuRepository.upsert_atomic（ON CONFLICT）+ StyleRepository
- 配置：复用 U06a IMPORT_MAX_*（无新配置）
- 指标：复用 U06a 5 指标（无新增）
- 测试：复用 U06a 测试基建（真实 adapter + 样本 CSV fixture + 同步任务调用）
- **无新依赖 / 无新服务 / 无新配置 / 无新指标**（U06b 是纯适配器层）

---

## 4. 文件影响预估（NFR Requirements 阶段仅文档）
- `aidlc-docs/construction/U06b/nfr-requirements/nfr-requirements.md`
- `aidlc-docs/construction/U06b/nfr-requirements/tech-stack-decisions.md`

---

**等待用户回复"继续"批准本计划（含预填的 10 个 [Answer]），开始生成 2 份 NFR 需求文档。**
