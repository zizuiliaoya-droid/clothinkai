# U06d 推广导入适配器 — 代码交付摘要

> 单元：U06d — 推广导入适配器（U06a 框架第 3 个业务 Adapter，最复杂）
> 状态：Code Generation 完成（单批）
> 验证：migration 001→010 全链路成功（无新 migration）；U06d 子集 19 测试全绿；全量 552 passed / 0 failed；覆盖率 78.87%；adapter 95%

---

## 1. 单元职责
让 PR 上传推广清单（CSV/XLSX）批量创建推广合作。**无新表/端点/Celery 任务/migration/权限**，唯一代码增量 = 1 个 adapter 模块。

## 2. 文件清单

| 文件 | 类型 | 职责 |
|---|---|---|
| `modules/importer/adapters/promotion.py` | 新建 | PromotionImportAdapter + _DEFAULT_COLUMNS(10) + _to_date + _to_decimal + _get_tenant_code 缓存 + register() |
| `tests/unit/test_promotion_adapter.py` | 新建 | parse_row + validate 纯函数（21 用例） |
| `tests/integration/test_import_promotion.py` | 新建 | 端到端真实 adapter（2 用例，seed style+blogger） |
| `aidlc-docs/U06d/code/{README,adapter-spec,test-coverage}.md` | 新建 | 文档 |

**不改**：main.py（已预置 adapters.promotion 路径）/ celery_app.py / migration / api / 权限 / U06a runner / adapters/__init__.py。

## 3. 实现要点（P-U06d-01，最复杂 Adapter）
- **INSERT-only**：每行建新 promotion（internal_code 系统生成，无文件业务键）；is_inserted 恒 True
- **2 必需 FK 解析**：style_code→style_id（StyleRepository.get_by_code）+ xiaohongshu_id→blogger_id（BloggerRepository.get_by_xiaohongshu_id）；sku_code 可选 FK；缺失 → RowValidationError → 行失败
- **internal_code 生成**：_get_tenant_code（实例级缓存）+ next_internal_sequence（U04 FB2 原子）+ format_internal_code
- **不经 U04 Service**：直接用 Repository（避免 Service commit/audit/重复检测 warning/权限与 runner per-row 事务 FB-C 冲突）
- **不自 commit**：复用 runner 传入 session；FK+sequence+INSERT 同 per-row 事务原子
- **快照**（style_code/short_name）+ **3 状态走 server_default 初始态** + pr_id=actor_id
- date（_to_date）+ Decimal（_to_decimal 禁 float）解析

## 4. Build & Test 结果
**adapter 一次实现通过，无生产 bug、无测试修复**（区别于 U06b/c 各修 1-2 个测试问题）。

## 5. 验收对齐
- ✅ PromotionImportAdapter 注册（source=manual_promotion）
- ✅ FK 解析（style/blogger 必需 + sku 可选）+ internal_code 生成 + 序号连续
- ✅ 端到端样本 CSV 跑通（建 promotion 初始态 + 缺 style/blogger failed → partial）
- ✅ 行级失败 → import_job.failed → retry only_failed
- ✅ 依赖 = U04 + U02 + U03 + U06a（不改框架，无新表/端点/migration）
- ⚠️ 已知限制：INSERT-only 跨文件相同推广会重复（文档化，V1 评估 dedup 键）
