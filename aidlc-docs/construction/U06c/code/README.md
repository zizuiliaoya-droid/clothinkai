# U06c 博主导入适配器 — 代码交付摘要

> 单元：U06c — 博主导入适配器（U06a 框架第 2 个业务 Adapter）
> 状态：Code Generation 完成（单批）
> 验证：migration 001→010 全链路成功（无新 migration）；U06c 子集 22 测试全绿；全量 533 passed / 0 failed；覆盖率 78.50%；adapter 99%

---

## 1. 单元职责
让运营上传博主清单（CSV/XLSX）批量导入博主库。**无新表/端点/Celery 任务/migration/权限**，唯一代码增量 = 1 个 adapter 模块。

## 2. 文件清单

| 文件 | 类型 | 职责 |
|---|---|---|
| `modules/importer/adapters/blogger.py` | 新建 | BloggerImportAdapter + _DEFAULT_COLUMNS(13) + _split_tags + _to_int + _to_decimal + register() |
| `tests/unit/test_blogger_adapter.py` | 新建 | parse_row + validate 纯函数（21 用例） |
| `tests/integration/test_import_blogger.py` | 新建 | 端到端真实 adapter（2 用例） |
| `aidlc-docs/U06c/code/{README,adapter-spec,test-coverage}.md` | 新建 | 文档 |

**不改**：main.py（已预置 adapters.blogger 路径）/ celery_app.py / migration / api / 权限 / U06a runner / adapters/__init__.py（U06b 已建）。

## 3. 实现要点（P-U06c-01）
- **单实体**：一行 = 一个 Blogger，单次 `BloggerRepository.upsert_atomic`（ON CONFLICT xiaohongshu_id，复用 U03）
- **不经 U03 Service**：直接用 BloggerRepository（避免 Service commit/audit/权限与 runner per-row 事务 FB-C 冲突，worker 无 HTTP User）
- **不自 commit**：复用 runner 传入 session（NF-1 per-row SET LOCAL）
- **多类型解析**：list（_split_tags `;；,，` → JSONB 数组）+ int（follower_count）+ Decimal（quote，禁 float）
- **platform 默认**：空 → 显式传 "小红书"（防 ON CONFLICT UPDATE 覆盖既有值）
- **actor_id 不写业务表**（U03 blogger 无 created_by）

## 4. Build & Test 修复（真实跑测发现）

| 问题 | 根因 | 修复 |
|---|---|---|
| test_tags_jsonb 标签少一个 | 测试 CSV 中 `美妆;护肤,穿搭` 含未加引号的逗号，被 CSV parser 当列分隔，`穿搭` 溢出到第 4 列丢失 | 测试 CSV 该字段加引号 `"美妆;护肤,穿搭"` |

> 测试数据问题，非生产代码 bug。adapter 实现一次通过。

## 5. 验收对齐
- ✅ BloggerImportAdapter 注册（source=manual_blogger）
- ✅ 博主字段映射（默认 + 自定义；标签 JSONB 数组 + int + Decimal）
- ✅ 端到端样本 CSV 跑通（新建 + 同 ID UPDATE + 缺 ID failed → partial）
- ✅ 行级失败 → import_job.failed → retry only_failed
- ✅ 依赖 = U03 + U06a（不改框架，无新表/端点/migration）
