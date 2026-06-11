# U06b 商品/SKU 导入适配器 — 代码交付摘要

> 单元：U06b — 商品/SKU 导入适配器（U06a 框架首个业务 Adapter）
> 状态：Code Generation 完成（单批）
> 验证：migration 全链路 001→010 成功（无新 migration）；U06b 子集 17 测试全绿；全量 511 passed / 0 failed；覆盖率 78.17%；adapter 96%

---

## 1. 单元职责

U06b 实现 U06a 框架的第一个业务 Adapter，让运营能上传"款式-SKU 平铺表"（CSV/XLSX）批量导入商品与 SKU。**无新表/端点/Celery 任务/migration/权限**，唯一代码增量是 1 个 adapter 模块。

---

## 2. 文件清单

| 文件 | 类型 | 职责 |
|---|---|---|
| `modules/importer/adapters/__init__.py` | 新建 | 适配器子包说明 |
| `modules/importer/adapters/style_sku.py` | 新建 | StyleSkuImportAdapter + _DEFAULT_COLUMNS + _to_decimal + _resolve_brand + register() |
| `tests/unit/test_style_sku_adapter.py` | 新建 | parse_row + validate 纯函数（20 用例） |
| `tests/integration/test_import_style_sku.py` | 新建 | 端到端：真实 adapter → runner → style/sku 入库（2 用例） |
| `aidlc-docs/U06b/code/{README,adapter-spec,test-coverage}.md` | 新建 | 文档 |

**不改**：main.py（register_import_adapters 已预置 style_sku 路径）/ celery_app.py / migration / api / 权限 / U06a runner。

---

## 3. 实现要点（P-U06b-01）

- **一行 = Style 复用/创建 + Sku upsert**：style 按 style_code `get_by_code` 命中复用（不覆盖既有资料）/ 未命中 `add+flush`；sku `upsert_atomic`（ON CONFLICT，复用 U02 P-U02-03）
- **不经 U02 Service**：直接用 StyleRepository / SkuRepository（Service 自带 commit/audit/权限与 runner per-row 事务边界 FB-C 冲突，且 worker 无 HTTP User）
- **不自 commit**：复用 runner 传入 session（runner 持有 per-row 事务 + SET LOCAL，NF-1）
- **style+sku 同事务原子**：sku 失败 → 整行回滚含新建 style（不留孤儿，集成测试 ST..B 验证）
- **Decimal 禁 float**：`_to_decimal`（去千分位 + Decimal；非法值保留原串供 validate）
- **brand 软关联**：brand_code 查不到 → None 不报错
- **mapping 双路**：mapping 非 None → mapping_config；None → 内置 _DEFAULT_COLUMNS（12 列）
- **注册**：模块级 register()，由 U06a register_import_adapters 双进程加载（NF-4）

---

## 4. Build & Test 修复（真实跑测发现）

| 问题 | 根因 | 修复 |
|---|---|---|
| test_end_to_end sku Decimal 断言失败 | sku_code 排序 `-红-L` 在 `-红-M` 前，skus[0] 非预期行 | 改断言 cost_price 集合 `{1299.00, 39.90}`（与顺序无关） |
| test_retry_only_failed status=skipped | batch 已 partial（终态），直接调 runner 撞 `status!=processing` 守卫 | 测试内先 UPDATE batch→processing（模拟 retry 端点 claim_for_retry，NF-3） |

> 两者均为测试断言/编排问题，非生产代码 bug。adapter 实现一次通过。

---

## 5. 验收对齐
- ✅ StyleSkuImportAdapter 注册（source=manual_style_sku）
- ✅ 商品/SKU 字段映射（默认 + 自定义 mapping 双路）
- ✅ 端到端样本 CSV 跑通（新建 + 复用 style + 缺字段 failed → partial）
- ✅ 行级失败 → import_job.failed → retry only_failed 幂等（sku 不重复）
- ✅ 依赖 = U02 + U06a（不改框架，无新表/端点/migration）
