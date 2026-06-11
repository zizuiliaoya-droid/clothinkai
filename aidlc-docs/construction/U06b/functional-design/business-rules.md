# U06b 业务规则（Business Rules）

> 单元：U06b — 商品/SKU 导入适配器
> 范围：StyleSkuImportAdapter 的解析/校验/upsert 规则 + 与 U06a 框架的边界
> 框架级规则（去重 409 / 上传校验 / 重试 / 状态机）全部**继承 U06a**，本单元不重复定义，仅引用

---

## 1. 适配器标识与注册（BR-U06b-01~03）

| 规则 | 说明 |
|---|---|
| **BR-U06b-01** | source 标识固定 `manual_style_sku`；target_table 标识 `style+sku`（审计/展示用） |
| **BR-U06b-02** | 模块 `app.modules.importer.adapters.style_sku` 提供模块级 `register()`，由 U06a `register_import_adapters`（main.py lifespan + worker_process_init）双进程加载（U06a 已预置该模块路径，落地后自动生效） |
| **BR-U06b-03** | upload 时 source 白名单校验由 U06a 执行（`manual_style_sku ∈ registry.sources()`，未注册 → 422）；runner 内 `registry.get` 二次防御 |

---

## 2. 字段映射规则（BR-U06b-10~16）

| 规则 | 说明 |
|---|---|
| **BR-U06b-10** | mapping 来源优先级：batch.mapping_version 指定的 field_mapping（运营自定义）> 内置默认映射（domain-entities §4） |
| **BR-U06b-11** | 必填字段集：`style_code` / `style_name` / `category` / `sku_code` / `color` / `size`（缺任一 → 行校验失败） |
| **BR-U06b-12** | 可空字段：`brand_code` / `season` / `cost_price` / `purchase_price` / `base_price` / `sourcing_type` |
| **BR-U06b-13** | 数值字段（cost_price/purchase_price/base_price）：非空时去千分位逗号后必须可解析为 Decimal 且 ≥0；空字符串 → None |
| **BR-U06b-14** | `sourcing_type` 非空时必须 ∈ {自产, 采购, 代发}（对齐 U02 Sku.sourcing_type 语义）；空 → 默认"自产" |
| **BR-U06b-15** | 长度上限（对齐 U02 模型）：style_code ≤ 64 / style_name ≤ 255 / category ≤ 32 / sku_code ≤ 64 / color ≤ 64 / size ≤ 32 |
| **BR-U06b-16** | parse_row 是纯函数；raw_data（import_job）保留**原始未转换行**（供失败下载/重试，FB-E） |

---

## 3. 行校验矩阵（validate，BR-U06b-20）

`validate(parsed)` 返回错误描述列表（空=通过），逐项累加：

| 校验项 | 失败 error_detail 文案 |
|---|---|
| style_code 必填非空 | `款式编码不能为空` |
| style_name 必填非空 | `款式名称不能为空` |
| category 必填非空 | `类目不能为空` |
| sku_code 必填非空 | `SKU编码不能为空` |
| color 必填非空 | `颜色不能为空` |
| size 必填非空 | `尺码不能为空` |
| cost_price 可解析 ≥0 | `成本价必须为非负数字` |
| purchase_price 可解析 ≥0 | `采购价必须为非负数字` |
| base_price 可解析 ≥0 | `吊牌价必须为非负数字` |
| sourcing_type 白名单 | `货源类型必须为 自产/采购/代发 之一` |
| 各字段长度上限 | `<字段> 超过长度上限 N` |

> 校验失败 → runner 捕获写 `import_job.failed`（error_detail = 错误列表用 `; ` 连接），per-row 事务隔离，不影响其他行。

---

## 4. upsert 规则（BR-U06b-30~36）

| 规则 | 说明 |
|---|---|
| **BR-U06b-30** | 一行 = 一个 SKU + 其所属 Style（按 style_code 关联） |
| **BR-U06b-31** | **Style 复用优先**：按 `(tenant_id, style_code, is_deleted=false)` 查；存在 → 复用 id，**不更新** style 字段（导入不覆盖既有款式资料，Q4） |
| **BR-U06b-32** | Style 不存在 → 创建最小 Style：style_code / style_name / category（+ season / brand_id 可空），design_status="大货"，is_active=true，is_deleted=false，owner_id=actor_id |
| **BR-U06b-33** | **Brand 软关联**：brand_code 非空 → 按 `(tenant_id, brand_code)` 查 brand_id；查到填入 style.brand_id，查不到**不报错、留空**（导入不自动建 brand，Q5） |
| **BR-U06b-34** | **Sku upsert**：`SkuRepository.upsert_atomic(tenant_id, values)` → `ON CONFLICT(tenant_id, sku_code) WHERE is_deleted=false DO UPDATE`；INSERT 路径 is_inserted=True，UPDATE 路径 False（复用 U02 P-U02-03） |
| **BR-U06b-35** | upsert 不更新的字段（ON CONFLICT 排除）：id / tenant_id / created_at / style_id / sku_code / is_deleted（复用既有 sku 时不改其所属 style 与软删状态） |
| **BR-U06b-36** | 返回 `(sku.id, sku_is_inserted)`；target_resource_id = sku.id |

---

## 5. 事务与租户上下文（BR-U06b-40~42，继承 U06a FB-C / NF-1）

| 规则 | 说明 |
|---|---|
| **BR-U06b-40** | adapter.upsert **不自行 commit / rollback**；runner 持有 per-row 事务边界（成功 commit，失败 rollback + 独立 bypass session 写 failed job） |
| **BR-U06b-41** | runner 在 per-row 事务内已设 `SET LOCAL app.tenant_id`（NF-1，事务级，防连接池串租）；adapter 用 runner 传入的 session，受 RLS 约束 |
| **BR-U06b-42** | adapter 创建 Style 时不显式写 tenant_id（U01 ORM before_flush 钩子注入）；调用 `upsert_atomic` 时显式传 tenant_id（U02 既有签名要求） |

---

## 6. 错误码与失败处理（BR-U06b-50~52）

| 规则 | 说明 |
|---|---|
| **BR-U06b-50** | 行级失败（校验失败 / upsert 异常）→ `import_job.failed` + error_detail，**不冒泡 HTTP**（runner 内捕获）；batch 终态按汇总 partial/completed/failed |
| **BR-U06b-51** | upload 层错误（格式/大小/source 白名单/去重 409）全部由 U06a 处理，U06b 不重复 |
| **BR-U06b-52** | 重试语义继承 U06a FB-E：行级失败 batch=partial → retry only_failed（原地更新 attempt_count）；本单元无"解析阶段失败"特有逻辑（解析由 U06a runner 的 _parse_rows 完成） |

---

## 7. 与 U06a 框架的边界（BR-U06b-60）

| U06b 做 | U06b 不做（U06a 已提供） |
|---|---|
| StyleSkuImportAdapter 三方法 + register() | upload / batches / retry / errors 下载 / field-mapping 端点 |
| manual_style_sku 默认映射 + 校验/转换 | file_hash 去重（UNIQUE）/ 状态机 / 重试编排 / CSV 解析 |
| Style 复用·创建 + Sku upsert（复用 U02 仓储） | run_import_batch runner（per-row 事务 / SET LOCAL / 双 session / 汇总） |
| 端到端样本 CSV 验收 | import_batch / import_job / field_mapping 表 / migration |

> **不改 U06a runner、不新增表/端点/Celery 任务、不新增权限 scope**（复用 importer.batch:read/write + importer.mapping:write）。

---

## 8. 验收对齐（unit-of-work U06b）
- ✅ StyleSkuImportAdapter 注册到 framework（source=manual_style_sku，BR-01~03）
- ✅ 商品/SKU 字段映射（§2 默认映射 + 可自定义覆盖）
- ✅ 端到端样本 CSV 跑通（§4 复用/创建 style + upsert sku）
- ✅ 行级失败 → import_job.failed → 下载 + only_failed 重试（§6，继承 U06a FB-E）
- ✅ 依赖 = U02 + U06a（§7 不改框架）
