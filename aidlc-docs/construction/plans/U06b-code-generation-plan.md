# U06b 代码生成计划（Code Generation Plan）

> 单元：U06b — 商品/SKU 导入适配器
> 阶段：MVP 第 7 个 sub-unit（导入并行支线，首个业务 Adapter）
> 节奏：**单批生成**（适配器层，无 migration / 无 main.py 改动 / 无新端点；唯一代码增量 = adapters/style_sku.py + 测试）
> 依赖：U02（style/sku/brand + Repository）+ U06a（ImportAdapter 协议 + Registry + runner + register_import_adapters 已预置模块路径）

---

## 1. 单元上下文

### 1.1 覆盖故事
EP07-S07~S10（与 U06a/c/d/e 共享）；U06b 额外验收 = 商品/SKU 字段映射 + 端到端样本 CSV 跑通

### 1.2 设计守护（来自 4 设计阶段）

| 守护 | 落地点 |
|---|---|
| FB-C adapter 不自 commit | upsert 仅用 runner 传入 session |
| NF-1 per-row SET LOCAL | 复用 U06a runner（adapter 无需关心） |
| P-U06b-01 单行两实体 upsert | style get-or-create + sku upsert_atomic 同事务 |
| BR-U06b-31 style 复用不覆盖 | get_by_code 命中仅用 id |
| BR-U06b-33 brand 软关联 | 查不到 None 不报错 |
| BR-U06b-13 Decimal 禁 float | _to_decimal（标准库 Decimal + 去千分位） |
| Q1@NFR-design 不经 U02 Service | 直接用 StyleRepository / SkuRepository |
| Q7 内置默认 vs field_mapping | parse_row columns 双路 |

### 1.3 项目结构

```
backend/app/modules/importer/adapters/      # U06b 新建子包
├── __init__.py                             # 包初始化（若 U06a 未建）
└── style_sku.py                            # StyleSkuImportAdapter + _DEFAULT_COLUMNS + _to_decimal + _resolve_brand + register()

backend/tests/
├── unit/test_style_sku_adapter.py          # parse_row + validate 纯函数
└── integration/test_import_style_sku.py    # 端到端：注册真实 adapter → upload → runner → style/sku 入库

backend/tests/conftest.py                   # 修改：manual_style_sku 样本 CSV fixture（可选，或测试内联）

aidlc-docs/construction/U06b/code/
├── README.md
├── adapter-spec.md
└── test-coverage.md

# 不改：main.py（register_import_adapters 已预置 style_sku 路径）/ celery_app.py / migration / api / 权限
```

---

## 2. 执行步骤（单批）

### Step 1 — 适配器实现（2 文件）
- [x] 1.1 `adapters/__init__.py`（包说明；若 U06a 已建则补充注释）
- [x] 1.2 `adapters/style_sku.py`：
  - `_DEFAULT_COLUMNS`（12 列内置默认映射）+ `_REQUIRED` / `_SOURCING` / `_DECIMAL_FIELDS` / 长度上限常量
  - `_to_decimal`（去千分位 + Decimal，禁 float；非法 → 保留原串供 validate）
  - `StyleSkuImportAdapter.parse_row`（mapping 非 None → mapping_config，None → _DEFAULT_COLUMNS）
  - `.validate`（必填 6 项 + 数值非负 + sourcing_type 白名单 + 长度上限）
  - `.upsert`（StyleRepository.get_by_code 复用 / add+flush 创建 + _resolve_brand + SkuRepository.upsert_atomic → (sku.id, is_inserted)）
  - `_resolve_brand`（brand_code → brand_id，查不到 None）
  - `register()`（ImportAdapterRegistry.register）

### Step 2 — 测试（2 文件 + conftest）
- [x] 2.1 `tests/unit/test_style_sku_adapter.py`：
  - parse_row：默认映射 / 自定义 mapping / Decimal 千分位 / 空值 / str strip
  - validate：通过 / 各必填缺失 / 负数 / 非 Decimal / sourcing_type 非法 / 超长
- [x] 2.2 `tests/integration/test_import_style_sku.py`（复用 U06a test_import_runner 模式）：
  - 注册真实 StyleSkuImportAdapter → upload 样本 CSV → _run_import_batch
  - 断言：新建 style+sku（success）/ 复用既有 style + 新 sku / 缺 sku_code → failed / batch=partial
  - Decimal 精度（cost_price=Decimal）/ brand 软关联（查到填 / 查不到 None）/ tenant_id 正确（跨租户）
  - retry only_failed（缺字段行重跑仍 failed，幂等）
- [x] 2.3 conftest.py：manual_style_sku 样本 CSV bytes fixture（或测试内联）

### Step 3 — 文档 + 完成校验
- [x] 3.1 `aidlc-docs/U06b/code/`：README.md + adapter-spec.md + test-coverage.md
- [x] 3.2 全部诊断器无警告 + AST 验证 + Plan 全 [x]
- [x] 3.3 故事追溯 EP07-S07~S10 + 设计守护测试映射

### Step 4 — Build & Test（真实环境）
- [x] 4.1 Docker（PG16 + Redis7 + Py3.12）：alembic upgrade head（应为 no-op，head=010）
- [x] 4.2 U06b 子集测试（unit + integration）跑通
- [x] 4.3 全量回归（确认 register style_sku 后 U06a 框架仍绿；adapter 注册不破坏 main 启动）
- [x] 4.4 清理临时容器/脚本

---

## 3. 故事追溯矩阵

| 故事 | 实施 | 测试 |
|---|---|---|
| EP07-S07 上传 | upload(source=manual_style_sku) + adapter.upsert | test_import_style_sku::test_end_to_end |
| EP07-S08 去重 | 复用 U06a hash 去重（框架层） | （U06a 已覆盖） |
| EP07-S09 映射版本 | parse_row 默认/自定义双路 | test_style_sku_adapter::test_parse_with_custom_mapping |
| EP07-S10 失败下载/重试 | validate 失败 → import_job.failed → retry only_failed | test_import_style_sku::test_partial_and_retry |

---

## 4. 设计守护测试矩阵

| 守护 | 测试 |
|---|---|
| P-U06b-01 单行两实体 | test_import_style_sku::test_new_style_and_sku + test_reuse_existing_style |
| BR-U06b-31 复用不覆盖 | test_reuse_existing_style（既有 style 字段不变） |
| BR-U06b-33 brand 软关联 | test_brand_resolve（查到/查不到） |
| BR-U06b-13 Decimal 禁 float | test_style_sku_adapter::test_decimal_parse + 集成断言 isinstance Decimal |
| FB-C 不自 commit | test_import_style_sku（runner 控制事务，行失败隔离） |
| 跨租户 tenant_id | test_import_style_sku::test_tenant_isolation |
| sku 幂等 | test_import_style_sku::test_retry_idempotent（重跑不重复 sku） |

---

## 5. 节奏决策

**单批**（vs U06a 3 批）：U06b 唯一代码增量是 1 个 adapter 模块（无 migration / 无 main 改动 / 无新端点 / 无新表）。复杂度低，单批生成 + 真实 Build & Test 即可。

| 批次 | 范围 | 文件数 |
|---|---|---|
| 单批 | adapter + 测试 + 文档 + Build&Test | 2 代码 + 2 测试 + 3 文档 |

---

**等待用户回复"继续"或"A"批准单批节奏，开始 U06b 代码生成。**
