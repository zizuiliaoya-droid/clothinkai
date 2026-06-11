# U06e 代码生成计划（Code Generation Plan）

> 单元：U06e — 结算导入适配器（导入支线最后一个 Adapter）
> 节奏：**单批生成**（唯一代码增量 = adapters/settlement.py + 测试；无 migration/无 main.py 改动）
> 依赖：U04（promotion get_by_internal_code）+ U05（SettlementRepository + next_settlement_sequence + format_settlement_no + SettlementStatus）+ U06a（框架）

---

## 1. 单元上下文

### 1.1 覆盖故事
EP07-S07~S10（共享）；额外验收 = 历史结算迁移 promotion 派生 + settlement_no 生成 + 端到端样本 CSV

### 1.2 设计守护（P-U06e-01）

| 守护 | 落地 |
|---|---|
| FB-C 不自 commit | upsert 仅用 runner 传入 session |
| NF-1 per-row SET LOCAL | 复用 U06a runner |
| P-U06e-01 INSERT-only + promotion 派生 | get_by_internal_code → 派生 blogger/style/pr + 序列 + INSERT |
| UNIQUE(promotion_id) 冲突 | catch IntegrityError → RowValidationError（FB3 不覆盖） |
| 合成 event_id | request_event_id = uuid4() |
| 不触发事件 | 不调 event_bus.dispatch |
| date/Decimal 禁 float + status 枚举 | _to_date / _to_decimal / _VALID_STATUS |
| 不经 U05 Service | 直接用 Repository |

### 1.3 项目结构
```
backend/app/modules/importer/adapters/settlement.py   # 新建
backend/tests/unit/test_settlement_adapter.py          # 新建
backend/tests/integration/test_import_settlement.py    # 新建（seed promotion + 已有 settlement）
aidlc-docs/construction/U06e/code/{README,adapter-spec,test-coverage}.md
# 不改：main.py（已预置 adapters.settlement）/ celery_app.py / migration / api / 权限 / runner
```

---

## 2. 执行步骤（单批）

### Step 1 — 适配器实现（1 文件）
- [x] 1.1 `adapters/settlement.py`：_DEFAULT_COLUMNS(9) + _to_date + _to_decimal + _VALID_STATUS + SettlementImportAdapter（parse_row/validate/upsert/_get_tenant_code）+ register()

### Step 2 — 测试（2 文件）
- [x] 2.1 `tests/unit/test_settlement_adapter.py`：_to_date + _to_decimal + parse_row（默认/自定义）+ validate（必填/数值/date/status 枚举各分支）
- [x] 2.2 `tests/integration/test_import_settlement.py`：seed promotion(+blogger+style) + 已有 settlement（模拟事件创建）→ upload 样本 CSV → _run_import_batch → settlement 入库 + settlement_no 生成 + 派生 blogger/style + 重复 promotion failed + 缺 promotion failed + 不触发事件（event_capture 空）+ partial + tenant_id；清理 settlement/settlement_sequence/seed

### Step 3 — 文档 + 完成校验
- [x] 3.1 `aidlc-docs/U06e/code/`：README + adapter-spec + test-coverage
- [x] 3.2 全部诊断器无警告 + AST 验证 + Plan 全 [x]
- [x] 3.3 故事追溯 + 设计守护测试映射

### Step 4 — Build & Test（真实环境）
- [x] 4.1 Docker（PG16 + Redis7 + Py3.12）：alembic upgrade head（no-op，head=010）
- [x] 4.2 U06e 子集（unit + integration）
- [x] 4.3 全量回归（确认注册 4 个 adapter 后框架仍绿）
- [x] 4.4 清理临时容器/脚本

---

## 3. 故事追溯矩阵

| 故事 | 实施 | 测试 |
|---|---|---|
| EP07-S07 上传 | upload(source=manual_settlement) + adapter.upsert | test_import_settlement::test_end_to_end |
| EP07-S08 去重 | 复用 U06a hash + UNIQUE(promotion_id) | test_import_settlement::test_duplicate_promotion |
| EP07-S09 映射 | parse_row 默认/自定义双路 | test_settlement_adapter::test_custom_mapping |
| EP07-S10 失败/重试 | promotion 缺失/UNIQUE 冲突 → import_job.failed | test_import_settlement::test_missing_promotion |

---

## 4. 设计守护测试矩阵

| 守护 | 测试 |
|---|---|
| P-U06e-01 INSERT-only + promotion 派生 | test_import_settlement::test_end_to_end（派生 blogger/style 校验） |
| UNIQUE(promotion_id) 冲突 catch | test_import_settlement::test_duplicate_promotion |
| 合成 event_id | test_import_settlement（request_event_id 非空且唯一） |
| 不触发事件 | test_import_settlement::test_no_events（event_capture 空） |
| date/Decimal/status | test_settlement_adapter::test_to_date/test_to_decimal/test_status_enum |
| 跨租户 tenant_id | test_import_settlement（settlement.tenant_id == batch.tenant_id） |

---

## 5. 节奏决策
单批（同 U06b/c/d）：唯一代码增量 1 个 adapter 模块。

---

**等待用户回复"继续"或"A"批准单批节奏，开始 U06e 代码生成。**
