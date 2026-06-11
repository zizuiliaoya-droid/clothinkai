# U06c 代码生成计划（Code Generation Plan）

> 单元：U06c — 博主导入适配器
> 节奏：**单批生成**（适配器层，唯一代码增量 = adapters/blogger.py + 测试；无 migration/无 main.py 改动/无新端点）
> 依赖：U03（blogger + BloggerRepository.upsert_atomic）+ U06a（协议 + Registry + runner + register_import_adapters 已预置 adapters.blogger 路径）

---

## 1. 单元上下文

### 1.1 覆盖故事
EP07-S07~S10（与 U06a/b/d/e 共享）；额外验收 = 博主字段映射 + 端到端样本 CSV

### 1.2 设计守护（P-U06c-01 + 继承）

| 守护 | 落地 |
|---|---|
| FB-C 不自 commit | upsert 仅用 runner 传入 session |
| NF-1 per-row SET LOCAL | 复用 U06a runner |
| P-U06c-01 单实体 upsert | 单次 BloggerRepository.upsert_atomic |
| 标签 list → JSONB | _split_tags（`;；,，`） |
| int/Decimal 禁 float | _to_int / _to_decimal |
| platform 默认不被覆盖 | upsert values 显式 "小红书" |
| 不经 U03 Service | 直接用 BloggerRepository |

### 1.3 项目结构

```
backend/app/modules/importer/adapters/
└── blogger.py                              # 新建：BloggerImportAdapter + _DEFAULT_COLUMNS + _split_tags + _to_int + _to_decimal + register()
                                            # （__init__.py 已由 U06b 建）
backend/tests/
├── unit/test_blogger_adapter.py            # 新建：parse_row + validate 纯函数
└── integration/test_import_blogger.py      # 新建：端到端真实 adapter

aidlc-docs/construction/U06c/code/
├── README.md
├── adapter-spec.md
└── test-coverage.md

# 不改：main.py（已预置 adapters.blogger）/ celery_app.py / migration / api / 权限 / U06a runner
```

---

## 2. 执行步骤（单批）

### Step 1 — 适配器实现（1 文件）
- [x] 1.1 `adapters/blogger.py`：_DEFAULT_COLUMNS（13 列）+ _split_tags + _to_int + _to_decimal + BloggerImportAdapter（parse_row/validate/upsert）+ register()

### Step 2 — 测试（2 文件）
- [x] 2.1 `tests/unit/test_blogger_adapter.py`：_split_tags（多分隔符/空）+ _to_int（千分位/非法）+ _to_decimal + parse_row（默认/自定义 mapping）+ validate（必填/数值/长度各分支）
- [x] 2.2 `tests/integration/test_import_blogger.py`：注册真实 adapter → upload 样本 CSV → _run_import_batch → blogger 入库 + category_tags JSONB + follower int + quote Decimal + 同 ID UPDATE 幂等 + partial + tenant_id

### Step 3 — 文档 + 完成校验
- [x] 3.1 `aidlc-docs/U06c/code/`：README + adapter-spec + test-coverage
- [x] 3.2 全部诊断器无警告 + AST 验证 + Plan 全 [x]
- [x] 3.3 故事追溯 + 设计守护测试映射

### Step 4 — Build & Test（真实环境）
- [x] 4.1 Docker（PG16 + Redis7 + Py3.12）：alembic upgrade head（no-op，head=010）
- [x] 4.2 U06c 子集（unit + integration）
- [x] 4.3 全量回归（确认注册 blogger adapter 后框架仍绿）
- [x] 4.4 清理临时容器/脚本

---

## 3. 故事追溯矩阵

| 故事 | 实施 | 测试 |
|---|---|---|
| EP07-S07 上传 | upload(source=manual_blogger) + adapter.upsert | test_import_blogger::test_end_to_end |
| EP07-S08 去重 | 复用 U06a hash | （U06a 已覆盖） |
| EP07-S09 映射 | parse_row 默认/自定义双路 | test_blogger_adapter::test_custom_mapping |
| EP07-S10 失败/重试 | validate 失败 → import_job.failed | test_import_blogger::test_partial |

---

## 4. 设计守护测试矩阵

| 守护 | 测试 |
|---|---|
| P-U06c-01 单实体 upsert | test_import_blogger::test_end_to_end |
| 标签 list → JSONB | test_blogger_adapter::test_split_tags + 集成断言 category_tags |
| int/Decimal 禁 float | test_blogger_adapter::test_to_int/test_to_decimal |
| 同 ID UPDATE 幂等 | test_import_blogger::test_duplicate_id_updates |
| 跨租户 tenant_id | test_import_blogger（blogger.tenant_id == batch.tenant_id） |

---

## 5. 节奏决策
单批（同 U06b）：唯一代码增量 1 个 adapter 模块（无 __init__ 新建，U06b 已建）。

---

**等待用户回复"继续"或"A"批准单批节奏，开始 U06c 代码生成。**
