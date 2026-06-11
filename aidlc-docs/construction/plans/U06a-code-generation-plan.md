# U06a 代码生成计划（Code Generation Plan）

> 单元：U06a — 统一导入框架  
> 阶段：MVP 第 6 个单元（导入并行支线，依赖仅 U01）  
> 节奏：**Plan A 分 3 批**（中等复杂度；含 FB-A~E + NF-1~6 全部反馈守护）  
> 关键风险点：异步 runner（per-row 事务 + SET LOCAL）/ DB 先行上传 + 补偿 / 原子 claim 互斥 / 双进程 Adapter 注册

---

## 1. 单元上下文

### 1.1 覆盖故事
EP07-S07（上传）/ S08（hash 去重）/ S09（字段映射版本）/ S10（失败下载与重试）

### 1.2 依赖
- **强依赖**：U01（R2 helper + Celery + 多租户 + 审计 + 异常 + metrics 框架）
- **不依赖**：U05 attachment ORM（FB-A，用 U01 R2 helper）
- **被依赖**：U06b/c/d/e（注册 Adapter）/ U13（自动采集复用 run_import_batch）

### 1.3 反馈守护（2 轮 11 条）

| 反馈 | 守护点 |
|---|---|
| FB-A | 不引用 Attachment ORM；用 U01 upload_bytes/get_object_bytes |
| FB-C | Adapter 签名 upsert(session, tenant_id, actor_id)；runner 持有事务 |
| FB-D | upload 直接 processing；UNIQUE(tenant,source,file_hash) |
| FB-E | retry 两类分流 + UNIQUE(batch_id,row_number) + attempt_count |
| **NF-1** | per-row 事务内 SET LOCAL app.tenant_id（非会话级） |
| **NF-2** | DB 先行 + UNIQUE 原子去重 + R2 失败补偿删除 |
| **NF-3** | 原子 claim_for_retry（UPDATE WHERE status IN(partial,failed) RETURNING） |
| **NF-4** | celery_app autodiscover import_tasks + worker_process_init 注册 |
| **NF-5** | importer.batch:read/write + importer.mapping:write + default_roles seed |
| **NF-6** | nginx 21m + handler 兜底 + 解析行数三层 |

### 1.4 项目结构

```
backend/app/modules/importer/            # U06a 新模块
├── __init__.py
├── enums.py                             # ImportBatchStatus(4) / ImportJobStatus(2)
├── permissions.py                       # importer.batch:read/write + importer.mapping:write（NF-5）
├── exceptions.py                        # 12 业务异常
├── models.py                            # ImportBatch / ImportJob / FieldMapping（4 UNIQUE）
├── schemas.py                           # Pydantic（Response/Page/Filters/MappingCreate 等）
├── adapter.py                           # ImportAdapter Protocol（FB-C 签名）
├── registry.py                          # ImportAdapterRegistry
├── domain.py                            # csv_safe（CSV injection）+ compute_sha256 + safe_filename
├── repository.py                        # ImportBatchRepository（find_by_hash + claim_for_retry NF-3 + ...）+ ImportJobRepository + FieldMappingRepository
├── field_mapping_service.py            # 版本管理
├── service.py                           # ImportService（upload DB 先行 NF-2 + retry claim NF-3 + download csv_safe）
├── deps.py                              # ImportServiceDep / FieldMappingServiceDep
└── api.py                               # 8 端点

backend/app/tasks/import_tasks.py        # run_import_batch（per-row SET LOCAL NF-1 + worker_process_init 注册 NF-4）

backend/app/core/metrics.py             # 修改：+5 指标
backend/app/core/config.py              # 修改：+4 配置
backend/app/core/attachment.py          # 修改：+get_object_bytes 薄封装（FB-A）
backend/app/core/celery_app.py          # 修改：autodiscover +import_tasks（NF-4）
backend/app/modules/auth/default_roles.py  # 修改：importer.batch/mapping 权限（NF-5）
backend/app/main.py                     # 修改：register_import_adapters + import_router

backend/alembic/versions/010_u06a_create_import_tables.py  # 3 表 + 约束 + RLS + permission seed

backend/tests/conftest.py               # 修改：fake_import_adapter + import_batch_factory fixture
backend/tests/
├── unit/
│   ├── test_import_state_machine.py    # ImportBatchStatus 转移
│   ├── test_import_domain.py           # csv_safe + sha256 + safe_filename
│   └── test_import_registry.py         # register/get/sources/clear
├── integration/
│   ├── test_import_upload.py           # 去重 409 + 格式 422 + source 白名单 + DB 先行（NF-2）
│   ├── test_import_runner.py           # run_import_batch（FakeAdapter：解析→行级→汇总 + per-row 隔离）
│   ├── test_import_retry.py            # 两类分流 + 原子 claim（NF-3）+ only_failed
│   ├── test_import_field_mapping.py    # 版本切换（旧 active 下线）
│   ├── test_import_errors_download.py  # CSV + csv_safe injection 转义
│   └── test_import_tenant_isolation.py # 跨租户 RLS（NF-1 SET LOCAL 验证）
├── api/
│   └── test_import_api.py              # 鉴权 + OpenAPI 8 端点
└── （performance 留 U06b 真实 Adapter）

frontend/src/features/import/
├── types.ts
└── api.ts

aidlc-docs/construction/U06a/code/
├── README.md
├── api-endpoints.md
└── test-coverage.md

# CI/CD
.github/workflows/ci.yml                 # 修改：grep autodiscover import_tasks（NF-4，不阻塞框架）
frontend/nginx.conf                      # 修改：client_max_body_size 21m（NF-6）
```

---

## 2. 执行步骤（3 批）

### Batch 1 — Step 1-3: 基础 + 模型 + 框架契约（~11 文件）

#### Step 1 — 模块基础（4 文件）
- [x] 1.1 `__init__.py` / `enums.py`（ImportBatchStatus 4 / ImportJobStatus 2）
- [x] 1.2 `permissions.py`（importer.batch:read/write + importer.mapping:write，NF-5）
- [x] 1.3 `exceptions.py`（12 异常：SourceUnknown / FormatUnsupported / FileTooLarge / TooManyRows / DuplicateFile 409 / RetryExhausted 409 / BatchBusy 409 / BatchNotFound 404 / MappingVersionNotFound / MappingInvalid / StorageError 500 / RowValidationError）

#### Step 2 — 横切扩展（3 修改）
- [x] 2.1 `core/metrics.py`：+5 指标（import_batch_total / import_rows_total / import_batch_duration_seconds / import_file_size_bytes / import_retry_total）
- [x] 2.2 `core/config.py`：+4 配置（IMPORT_MAX_FILE_MB / IMPORT_MAX_ROWS / IMPORT_RETENTION_DAYS / IMPORT_BUCKET）
- [x] 2.3 `core/attachment.py`：+`get_object_bytes(bucket, key) -> bytes`（FB-A）

#### Step 3 — 模型 + 框架契约（4 文件）
- [x] 3.1 `models.py`（ImportBatch / ImportJob / FieldMapping + 4 UNIQUE + CHECK）
- [x] 3.2 `schemas.py`（ImportBatchResponse/Page/Filters + FieldMappingCreate/Response + ImportJobResponse）
- [x] 3.3 `adapter.py`（ImportAdapter Protocol，FB-C 签名）
- [x] 3.4 `registry.py`（ImportAdapterRegistry）

### Batch 2 — Step 4-6: Domain + Repository + Service + Runner（~6 文件 + main）

#### Step 4 — Domain + Repository（2 文件）
- [x] 4.1 `domain.py`（csv_safe CSV injection + compute_sha256 流式 + safe_filename）
- [x] 4.2 `repository.py`（ImportBatchRepository: find_by_hash + **claim_for_retry NF-3** + list_with_filters + get_failed_jobs；ImportJobRepository: upsert_row/原地更新；FieldMappingRepository）

#### Step 5 — Service + FieldMapping（2 文件）
- [x] 5.1 `field_mapping_service.py`（create_version 旧 active 下线 + get_active/by_version/list）
- [x] 5.2 `service.py`（ImportService: **upload DB 先行+补偿 NF-2** / get_batch / list_batches / **retry claim NF-3 两类分流 FB-E** / download_errors csv_safe）

#### Step 6 — Runner + API + main（3 文件 + 1 修改）
- [x] 6.1 `tasks/import_tasks.py`（**run_import_batch: per-row SET LOCAL NF-1 + 双 session + only_failed FB-E + worker_process_init 注册 NF-4**）
- [x] 6.2 `core/celery_app.py`：autodiscover +import_tasks（NF-4）
- [x] 6.3 `deps.py` + `api.py`（8 端点）
- [x] 6.4 `main.py`：register_import_adapters + import_router

### Batch 3 — Step 7-10: Migration + 测试 + Frontend + 文档（~16 文件 + 修改）

#### Step 7 — Migration + 权限 seed（2 文件/修改）
- [x] 7.1 `alembic/versions/010_u06a_create_import_tables.py`（3 表 + 4 UNIQUE + 索引 + 3 RLS + permission seed PL/pgSQL）
- [x] 7.2 `modules/auth/default_roles.py`：importer.batch/mapping 权限（NF-5，与 seed 一致）

#### Step 8 — 测试（~9 文件 + conftest）
- [x] 8.1 conftest.py：FakeImportAdapter + import_batch_factory fixture
- [x] 8.2 unit: test_import_state_machine / test_import_domain（csv_safe）/ test_import_registry
- [x] 8.3 integration: test_import_upload（去重 409 + DB 先行 NF-2）/ test_import_runner（FakeAdapter per-row）/ test_import_retry（claim NF-3 + 两类分流）/ test_import_field_mapping / test_import_errors_download（csv_safe）/ test_import_tenant_isolation（NF-1 SET LOCAL）
- [x] 8.4 api: test_import_api（鉴权 + OpenAPI 8 端点）

#### Step 9 — Frontend + CI/CD（2 + 2 修改）
- [x] 9.1 frontend/src/features/import/types.ts + api.ts
- [x] 9.2 ci.yml：grep autodiscover import_tasks（NF-4，不阻塞）
- [x] 9.3 frontend/nginx.conf：client_max_body_size 21m（NF-6）

#### Step 10 — 文档 + 完成校验
- [x] 10.1 aidlc-docs/U06a/code/README.md + api-endpoints.md + test-coverage.md
- [x] 10.2 全部诊断器无警告 + AST 验证 + Plan 全 [x]
- [x] 10.3 故事追溯 EP07-S07~S10 + 11 反馈守护测试映射

---

## 3. 故事追溯矩阵

| 故事 | 实施 | 测试 |
|---|---|---|
| EP07-S07 上传 | service.upload（DB 先行）+ run_import_batch | test_import_upload + test_import_runner |
| EP07-S08 hash 去重 | UNIQUE(tenant,source,file_hash) + IntegrityError→409 | test_import_upload::test_duplicate_409 |
| EP07-S09 映射版本 | field_mapping_service.create_version | test_import_field_mapping |
| EP07-S10 失败下载/重试 | service.download_errors（csv_safe）+ retry（claim + 两类分流） | test_import_errors_download + test_import_retry |

---

## 4. 反馈守护测试矩阵

| 反馈 | 守护测试 |
|---|---|
| FB-A | test_import_runner（get_object_bytes mock，不碰 attachment ORM） |
| FB-C/NF-1 | test_import_tenant_isolation（跨租户 SET LOCAL RLS 拦截）+ test_import_runner（per-row 隔离） |
| FB-D/NF-2 | test_import_upload::test_db_first_dedup（DB 先行 + 并发 409） |
| FB-E/NF-3 | test_import_retry（only_failed 原地更新 + claim 互斥 + retry_count 上限 409） |
| NF-4 | ci.yml grep + test_import_runner（任务可调用） |
| NF-5 | test_import_api（权限 403）+ migration seed |
| NF-6 | test_import_upload::test_too_large 422 + nginx 配置 |
| CSV injection | test_import_errors_download::test_csv_safe |

---

## 5. 节奏决策

**Plan A 3 批**（比 U04/U05 少 1 批，因 U06a 无前端复杂交互 + 框架层无业务 Adapter）：

| 批次 | 范围 | 文件数 | 复杂度 |
|---|---|---|---|
| Batch 1 | Step 1-3：基础 + 模型 + 契约 | ~11 | 低 |
| Batch 2 | Step 4-6：Domain + Repository + Service + **Runner** | ~6 + 修改 | **高**（runner per-row SET LOCAL + DB 先行 + claim 是 U06a 风险核心） |
| Batch 3 | Step 7-10：Migration + 测试 + Frontend + 文档 | ~16 + 修改 | 中 |

---

**等待用户回复"继续"或"A"批准 Plan A 节奏，开始 Batch 1 生成。**
