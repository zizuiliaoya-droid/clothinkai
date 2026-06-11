# U06d 代码生成计划（Code Generation Plan）

> 单元：U06d — 推广导入适配器
> 节奏：**单批生成**（唯一代码增量 = adapters/promotion.py + 测试；无 migration/无 main.py 改动）
> 依赖：U02（style/sku get_by_code）+ U03（blogger get_by_xiaohongshu_id）+ U04（PromotionRepository + next_internal_sequence + format_internal_code）+ U06a（框架）

---

## 1. 单元上下文

### 1.1 覆盖故事
EP07-S07~S10（共享）；额外验收 = FK 解析 + internal_code 生成 + 端到端样本 CSV

### 1.2 设计守护（P-U06d-01）

| 守护 | 落地 |
|---|---|
| FB-C 不自 commit | upsert 仅用 runner 传入 session |
| NF-1 per-row SET LOCAL | 复用 U06a runner |
| P-U06d-01 INSERT-only + FK 解析 | style/blogger 必需 raise + sku 可选 + sequence + INSERT |
| internal_code | next_internal_sequence（FB2）+ format_internal_code + tenant_code 缓存 |
| date/Decimal 禁 float | _to_date / _to_decimal |
| 不经 U04 Service | 直接用 Repository |
| 行级异常 | 复用 U06a RowValidationError（FK 缺失/序列溢出 → 该异常 → runner failed） |

### 1.3 项目结构
```
backend/app/modules/importer/adapters/promotion.py   # 新建
backend/tests/unit/test_promotion_adapter.py          # 新建
backend/tests/integration/test_import_promotion.py    # 新建（seed style+blogger）
aidlc-docs/construction/U06d/code/{README,adapter-spec,test-coverage}.md
# 不改：main.py（已预置 adapters.promotion）/ celery_app.py / migration / api / 权限 / runner
```

---

## 2. 执行步骤（单批）

### Step 1 — 适配器实现（1 文件）
- [x] 1.1 `adapters/promotion.py`：_DEFAULT_COLUMNS(10) + _to_date + _to_decimal + PromotionImportAdapter（parse_row/validate/upsert/_get_tenant_code 缓存）+ register()

### Step 2 — 测试（2 文件）
- [x] 2.1 `tests/unit/test_promotion_adapter.py`：_to_date（合法/非法/空）+ _to_decimal + parse_row（默认/自定义）+ validate（必填/数值/date 各分支）
- [x] 2.2 `tests/integration/test_import_promotion.py`：seed style+blogger（committed）→ upload 样本 CSV → _run_import_batch → promotion 入库 + internal_code 生成 + 序号连续 + 缺 style/blogger failed + sku 可选 + partial + tenant_id；清理 promotion/promotion_sequence/seed

### Step 3 — 文档 + 完成校验
- [x] 3.1 `aidlc-docs/U06d/code/`：README + adapter-spec + test-coverage
- [x] 3.2 全部诊断器无警告 + AST 验证 + Plan 全 [x]
- [x] 3.3 故事追溯 + 设计守护测试映射

### Step 4 — Build & Test（真实环境）
- [x] 4.1 Docker（PG16 + Redis7 + Py3.12）：alembic upgrade head（no-op，head=010）
- [x] 4.2 U06d 子集（unit + integration）
- [x] 4.3 全量回归（确认注册 3 个 adapter 后框架仍绿）
- [x] 4.4 清理临时容器/脚本

---

## 3. 故事追溯矩阵

| 故事 | 实施 | 测试 |
|---|---|---|
| EP07-S07 上传 | upload(source=manual_promotion) + adapter.upsert | test_import_promotion::test_end_to_end |
| EP07-S08 去重 | 复用 U06a hash | （U06a 已覆盖） |
| EP07-S09 映射 | parse_row 默认/自定义双路 | test_promotion_adapter::test_custom_mapping |
| EP07-S10 失败/重试 | FK 缺失 → import_job.failed | test_import_promotion::test_missing_fk_failed |

---

## 4. 设计守护测试矩阵

| 守护 | 测试 |
|---|---|
| P-U06d-01 INSERT-only + FK 解析 | test_import_promotion::test_end_to_end |
| internal_code 生成 + 序号连续 | test_import_promotion::test_internal_code_sequence |
| FK 缺失 raise → failed | test_import_promotion::test_missing_style/blogger |
| date/Decimal 禁 float | test_promotion_adapter::test_to_date/test_to_decimal |
| 跨租户 tenant_id | test_import_promotion（promotion.tenant_id == batch.tenant_id） |

---

## 5. 节奏决策
单批（同 U06b/c）：唯一代码增量 1 个 adapter 模块。

---

**等待用户回复"继续"或"A"批准单批节奏，开始 U06d 代码生成。**
