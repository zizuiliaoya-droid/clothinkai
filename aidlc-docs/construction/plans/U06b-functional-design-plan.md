# U06b 功能设计计划（Functional Design Plan）

> 单元：U06b — 商品/SKU 导入适配器
> 阶段：MVP 第 7 个 sub-unit（导入并行支线，首个业务 Adapter）
> 依赖：**U02（商品/SKU 表 + StyleRepository/SkuRepository.upsert_atomic）+ U06a（ImportAdapter 协议 + Registry + run_import_batch runner）**
> 节奏：单批生成（适配器层，复用 U06a 框架与 U02 仓储，无新表/无新状态机）

---

## 0. 单元定位与关键事实

U06b 是 **U06a 框架的第一个业务 Adapter**。它不新建表、不新建 API、不新建 Celery 任务，而是：
1. 实现一个 `StyleSkuImportAdapter`（满足 U06a 的 `ImportAdapter` Protocol，FB-C 签名）
2. 注册到 `ImportAdapterRegistry`（`register()`，由 main.py / worker_process_init 加载 — U06a 已就绪）
3. 定义 `manual_style_sku` 来源的字段映射规则（默认列映射 + 类型转换）
4. 提供一份端到端样本 CSV 跑通验收

### 0.1 上游既有能力（直接复用，不重做）

| 能力 | 来源 | U06b 用法 |
|---|---|---|
| `ImportAdapter` Protocol（parse_row / validate / upsert(session,tenant_id,actor_id)） | U06a adapter.py | 实现该协议 |
| `ImportAdapterRegistry.register/get/sources` | U06a registry.py | 注册 `manual_style_sku` |
| `run_import_batch`（per-row 事务 + SET LOCAL NF-1 + 双 session + 两类重试 FB-E） | U06a tasks/import_tasks.py | runner 调用本 adapter，**不改 runner** |
| upload / batches / retry / errors 下载 / field-mapping API（8 端点） | U06a api.py | **复用，不新增端点**；upload 传 `source=manual_style_sku` |
| `SkuRepository.upsert_atomic`（ON CONFLICT (tenant,sku_code) RETURNING is_inserted） | U02 repository.py | sku 行 upsert |
| `StyleRepository`（get/create + uq_style_code partial UNIQUE） | U02 repository.py | style 行解析/创建/复用 |
| field_mapping 版本管理（create_version / get_active） | U06a field_mapping_service.py | 创建 `manual_style_sku` v1 默认映射 |

### 0.2 U06b 的唯一增量

- `modules/importer/adapters/style_sku.py`（**新建** — StyleSkuImportAdapter + `register()`）
- main.py `register_import_adapters` 的 adapter_modules 列表已含 `app.modules.importer.adapters.style_sku`（U06a 已预置，缺失仅 warning）→ U06b 落地后自动注册
- 一份默认 field_mapping 种子（manual_style_sku v1）+ 样本 CSV（测试 fixture）
- 端到端测试（真实 adapter 跑通 upload→runner→style/sku 入库）

---

## 1. 覆盖故事

U06b 与 U06a/c/d/e 共享 **EP07-S07~S10** 故事与 GWT；U06b 的**额外验收** = 商品/SKU 目标表的字段映射 + 端到端样本 CSV 跑通（见 unit-of-work-plan「U06b~U06e 适配器故事覆盖」说明）。

| 故事 | U06b 体现 |
|---|---|
| EP07-S07 上传 | upload `source=manual_style_sku` → runner 调 StyleSkuImportAdapter |
| EP07-S08 去重 | 复用 U06a `UNIQUE(tenant,source,file_hash)`（框架层，无需改） |
| EP07-S09 映射版本 | manual_style_sku 默认映射 v1（商品编码→style_code 等） |
| EP07-S10 失败下载/重试 | 复用框架；行级失败（如 style_code 缺失）→ failed job → 下载/重试 |

---

## 2. 澄清问题（已预填合理默认值，请审阅 [Answer] 标签）

### Q1 — source 标识
[Answer] **`manual_style_sku`**（与 U06a register_import_adapters 预置模块路径 `app.modules.importer.adapters.style_sku` 一致）。target_table 审计展示用 `"style+sku"`（一行同时落 style + sku 两实体）。

### Q2 — 一行 CSV 的目标实体（关键设计）
[Answer] **一行 = 一个 SKU + 其所属 Style（按 style_code 复用/创建）**。导入文件是"款式-SKU 平铺表"（每行含 style_code/style_name/category + sku_code/color/size/价格）。upsert 逻辑：
1. 先按 `(tenant_id, style_code)` 找 style：存在则复用其 id；不存在则创建 style（最小字段）
2. 再按 `(tenant_id, sku_code)` `upsert_atomic` sku（绑定 style_id）
返回 `(sku_id, is_inserted)`（resource_id = sku.id；is_inserted 取 sku 路径）。

### Q3 — 字段映射（manual_style_sku v1 默认列）
[Answer] 默认映射（mapping_config.columns，源列名为中文表头）：

| source_col | target_field | required | type | 说明 |
|---|---|---|---|---|
| 款式编码 | style_code | ✅ | str | style 业务键 |
| 款式名称 | style_name | ✅ | str | style 创建用 |
| 类目 | category | ✅ | str | style.category |
| 品牌编码 | brand_code | — | str | 可空，按 (tenant,brand_code) 查 brand_id（不存在则忽略，留空） |
| 季节 | season | — | str | style.season |
| SKU编码 | sku_code | ✅ | str | sku 业务键 |
| 颜色 | color | ✅ | str | sku.color |
| 尺码 | size | ✅ | str | sku.size |
| 成本价 | cost_price | — | decimal | sku.cost_price（≥0） |
| 采购价 | purchase_price | — | decimal | sku.purchase_price（≥0） |
| 吊牌价 | base_price | — | decimal | sku.base_price（≥0） |
| 货源类型 | sourcing_type | — | str | sku.sourcing_type（默认"自产"，白名单 自产/采购/代发） |

> mapping 可由运营通过 U06a `POST /api/imports/field-mappings` 自定义覆盖；adapter 在 mapping=None 时用上述**内置默认映射**（恒等回退）。

### Q4 — style 复用 vs 创建策略
[Answer] **style_code 优先复用**：同 (tenant, style_code, is_deleted=false) 已存在 → 复用 id，**不更新** style 字段（避免导入意外覆盖既有款式资料）；不存在 → 创建最小 style（style_code/style_name/category[+season/brand_id]，design_status 默认"大货"，is_active=true）。理由：导入主语义是补 SKU；style 资料以系统内维护为准。V1 可加 `update_style_on_import` 开关。

### Q5 — brand 处理
[Answer] **软关联**：brand_code 非空时按 (tenant, brand_code) 查 brand_id；查到则填 style.brand_id；查不到 **不报错、留空**（brand 字典由 U02 维护，导入不自动建 brand）。brand_code 为空 → brand_id=None。

### Q6 — 行校验规则（validate，纯函数）
[Answer] 返回错误描述列表（空=通过）：
- 必填非空：style_code / style_name / category / sku_code / color / size
- 数值字段（cost_price/purchase_price/base_price）若非空必须可解析为 Decimal 且 ≥0
- sourcing_type 若非空必须 ∈ {自产, 采购, 代发}
- 长度上限（对齐 U02 模型）：style_code ≤ 64 / sku_code ≤ 64 / color ≤ 64 / size ≤ 32
> 校验失败 → runner 写 import_job.failed（error_detail = 错误列表 join），不影响其他行（per-row 事务）。

### Q7 — upsert 幂等与事务（FB-C 契约）
[Answer] `upsert(parsed, *, session, tenant_id, actor_id)` **不自行 commit**（runner 持有 per-row 事务 + SET LOCAL app.tenant_id，NF-1）。内部：
1. style：`SkuRepository`/`StyleRepository` 在传入 session 上操作（受 RLS 约束）；style 查不到则 `session.add(Style(...))` + `flush()` 拿 id
2. sku：`SkuRepository(session).upsert_atomic(tenant_id=..., values={...})` → (sku, is_inserted)
3. 返回 (sku.id, is_inserted)
> tenant_id 由 runner 的 SET LOCAL + ORM 钩子双重保证；adapter 不显式写 tenant_id（ORM before_flush 注入）但 upsert_atomic 需显式传 tenant_id（已有签名）。

### Q8 — 类型转换（parse_row，纯函数）
[Answer] 按 mapping.type 转换：str（strip）/ decimal（去千分位 + Decimal，空→None）/ 其余按 str。日期类字段本单元不涉及。空字符串 → None（数值/可空字段）。原始行 raw_data 保真存 import_job（不转换，供失败下载）。

### Q9 — 权限
[Answer] **复用 U06a `importer.batch:read/write`**（upload/retry=write，查询/下载=read）。U06b 不新增权限 scope；运营/PR/PR主管已在 U06a migration 010 seed 获得。mapping 创建用 `importer.mapping:write`。

### Q10 — 测试策略
[Answer] **真实 adapter 端到端**（U06a 用 FakeAdapter 测框架，U06b 测真实 StyleSkuImportAdapter）：
- unit：parse_row 类型转换 + validate 各失败分支（纯函数，无 DB）
- integration：注册 adapter → upload 样本 CSV → run_import_batch → 断言 style/sku 入库 + import_job 结果 + 部分行失败 partial + 重试 only_failed
- 样本 CSV fixture：含 2 正常行（新 style+sku / 复用 style+新 sku）+ 1 失败行（缺 sku_code）

### Q11 — 默认 field_mapping 种子落点
[Answer] **不在 migration 硬种子**（mapping 是租户级数据，各租户列名可能不同）。改为：adapter 在 mapping=None 时用**代码内置默认映射**（Q3 表）。运营如需自定义，通过 U06a field-mapping API 建 active 版本覆盖。测试中显式建 mapping 或依赖内置默认。

### Q12 — Adapter 注册时机
[Answer] **复用 U06a 双进程注册**（main.py lifespan + worker_process_init）。U06a 的 register_import_adapters 已含 `app.modules.importer.adapters.style_sku`（ModuleNotFoundError 仅 warning）；U06b 落地该模块 + 模块级 `register()` 函数后自动生效。无需改 main.py / celery_app.py。

---

## 3. 生成产物（3 份功能设计文档）

### 3.1 domain-entities.md
- **无新表**（复用 U02 style/sku/brand + U06a import_batch/import_job/field_mapping）
- StyleSkuImportAdapter 契约（source=manual_style_sku / target_table="style+sku" / parse_row / validate / upsert 三方法签名）
- manual_style_sku 默认字段映射表（Q3 完整列）+ mapping_config JSONB 结构示例
- 一行 → (style 复用/创建 + sku upsert) 的实体关系说明 + Mermaid（importer ↔ U02 style/sku）
- 类型转换与回退规则（mapping=None → 内置默认）

### 3.2 business-rules.md
- BR-U06b-01~：source 标识 / 一行=sku+style / style 复用不覆盖（Q4）/ brand 软关联（Q5）
- 字段映射规则（必填集 / 类型 / 长度 / 白名单 sourcing_type）
- 行校验矩阵（validate 各失败 → error_detail 文案）
- upsert 幂等（sku ON CONFLICT；style 复用）+ 事务契约（不 commit，runner 持有，FB-C/NF-1）
- 错误码：行级失败计 import_job.failed（不冒泡 HTTP）；upload/source 复用 U06a 错误码
- 与 U06a 框架的边界（不改 runner / 不新增端点 / 复用去重·重试·下载）

### 3.3 business-logic-model.md
- UC-1 注册（register() → ImportAdapterRegistry，main/worker 加载时序）
- UC-2 端到端导入（upload source=manual_style_sku → run_import_batch → parse_row → validate → upsert[style 复用/建 + sku upsert] → import_job → batch 汇总）
- UC-3 行级失败 + 重试（缺 sku_code → failed → 下载 CSV → retry only_failed 原地更新）
- UC-4 自定义映射覆盖（运营建 manual_style_sku v2 → 历史 batch 记 version）
- 端到端时序图（Mermaid）+ adapter 在 runner per-row 事务内的位置（SET LOCAL 已由 runner 设置）

---

## 4. 验收对齐（unit-of-work U06b 验收）
- ✅ StyleSkuImportAdapter 注册到 framework（source=manual_style_sku）
- ✅ 商品/SKU 字段映射配置（Q3 默认映射 + 可自定义覆盖）
- ✅ 端到端样本 CSV 跑通（upload → 入库 style+sku，含复用既有 style）
- ✅ 行级失败 → import_job.failed → 下载 + 仅重试 failed 行（复用 U06a FB-E）
- ✅ 依赖 = U02 + U06a（不改 U06a runner，不新增表/端点）

---

## 5. 文件影响预估（Functional Design 阶段仅文档）
- `aidlc-docs/construction/U06b/functional-design/domain-entities.md`
- `aidlc-docs/construction/U06b/functional-design/business-rules.md`
- `aidlc-docs/construction/U06b/functional-design/business-logic-model.md`

---

**等待用户回复"继续"批准本计划（含 12 个 [Answer]），开始生成 3 份功能设计文档。**
