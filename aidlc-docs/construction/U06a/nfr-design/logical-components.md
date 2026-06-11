# U06a 逻辑组件（Logical Components）

> 单元：U06a — 统一导入框架  
> 范围：U06a 新增/修改组件清单 + 启动序列 + 依赖图 + 与 U06b/c/d/e 注册契约

---

## 1. 组件清单

### 1.1 modules/importer/（新建模块）

| 组件 | 文件 | 职责 |
|---|---|---|
| 模块入口 | `__init__.py` | 模块说明 |
| 枚举 | `enums.py` | ImportBatchStatus(4) / ImportJobStatus(2) / ImportSource(占位) |
| 权限 | `permissions.py` | `importer.batch:read` / `importer.batch:write` / `importer.mapping:write`（NF-5） |
| 异常 | `exceptions.py` | ImportSourceUnknownError / ImportFormatUnsupportedError / ImportFileTooLargeError / ImportTooManyRowsError / ImportDuplicateFileError(409) / ImportRetryExhaustedError(409) / ImportBatchBusyError(409) / ImportBatchNotFoundError(404) / ImportMappingVersionNotFoundError / ImportMappingInvalidError / ImportStorageError(500) / RowValidationError |
| ORM 模型 | `models.py` | ImportBatch / ImportJob / FieldMapping（含 UNIQUE 约束：(tenant,source,file_hash) / (batch_id,row_number) / (tenant,source,version) / 部分 UNIQUE is_active） |
| Schema | `schemas.py` | ImportBatchResponse / ImportBatchPage / ImportBatchFilters / FieldMappingCreate / FieldMappingResponse / ImportJobResponse |
| Adapter 协议 | `adapter.py` | `ImportAdapter` Protocol（parse_row/validate/upsert(session,tenant_id,actor_id)，FB-C） |
| 注册中心 | `registry.py` | `ImportAdapterRegistry`（register/get/sources/clear） |
| 领域 | `domain.py` | `csv_safe`（CSV injection 转义）/ `compute_sha256`（流式）/ `safe_filename` |
| 仓储 | `repository.py` | ImportBatchRepository（find_by_hash / **claim_for_retry 原子（NF-3）** / list_with_filters / get_failed_jobs / FOR UPDATE）+ ImportJobRepository + FieldMappingRepository |
| 映射服务 | `field_mapping_service.py` | create_version（旧 active 下线 + 新建）/ get_active / get_by_version / list_versions |
| 导入服务 | `service.py` | ImportService（upload[DB 先行+补偿,NF-2] / get_batch / list_batches / retry[claim,NF-3] / download_errors[csv_safe]） |
| 依赖注入 | `deps.py` | ImportServiceDep / FieldMappingServiceDep |
| API | `api.py` | upload / list / get / retry / errors/download / field-mappings CRUD |

### 1.2 tasks/（新建）

| 组件 | 文件 | 职责 |
|---|---|---|
| 导入任务 | `tasks/import_tasks.py` | `run_import_batch(batch_id, only_failed)`（双 session + per-row SET LOCAL，NF-1）+ worker_process_init 注册 Adapter（NF-4） |

### 1.3 横切修改（core / main / celery / 部署）

| 组件 | 文件 | 改动 |
|---|---|---|
| 指标 | `core/metrics.py` | 追加 5 指标（import_batch_total / import_rows_total / import_batch_duration_seconds / import_file_size_bytes / import_retry_total） |
| 配置 | `core/config.py` | 追加 IMPORT_MAX_FILE_MB / IMPORT_MAX_ROWS / IMPORT_RETENTION_DAYS / IMPORT_BUCKET |
| R2 helper | `core/attachment.py` | 追加 `get_object_bytes(bucket, key) -> bytes`（U01 helper 扩展，不碰 Attachment ORM，FB-A） |
| Celery | `core/celery_app.py` | **autodiscover_tasks 加 `app.tasks.import_tasks`（NF-4）** |
| 应用入口 | `main.py` | 注册 import_router + `register_import_adapters()`（lifespan，NF-4） |
| 默认角色 | `modules/auth/default_roles.py` | operations += importer.batch:read；pr/pr_manager += importer.batch:write；pr_manager/admin += importer.mapping:write（NF-5） |
| 部署 | `frontend/nginx.conf` / Zeabur 配置 | `client_max_body_size 21m`（NF-6） |

### 1.4 数据库迁移

| migration | 内容 |
|---|---|
| `010_u06a_create_import_tables.py` | import_batch / import_job / field_mapping 3 表 + 约束 + 索引 + RLS（继承 TenantScopedModel 模式）+ permission seed 追加（importer.batch/mapping，NF-5） |

> 注：实际编号在 Code Generation 阶段按当时 head 确定（当前 head=009）。

---

## 2. 启动序列

```
[HTTP 进程] main.py lifespan startup:
  ├─ register_event_listeners()       (U04/U05)
  ├─ register_import_adapters()        ← U06a 新增（NF-4）
  │     try import U06b/c/d/e .register() → ImportAdapterRegistry.register(...)
  │     ModuleNotFoundError → warning（未部署不阻塞）
  └─ include_router(import_router)

[Worker 进程] celery_app worker_process_init 信号:
  └─ register_import_adapters()        ← NF-4 关键（HTTP 注册 worker 看不到）

[Worker 任务发现] celery_app.autodiscover_tasks([..., "app.tasks.import_tasks"])  ← NF-4
```

---

## 3. 依赖图

```
                    ┌─────────────────────┐
   HTTP ──upload──▶ │  ImportService      │──upload_bytes──▶ R2(private)  [U01 helper, FB-A]
                    │  (DB 先行+补偿,NF-2) │──INSERT batch──▶ import_batch  [UNIQUE 原子去重]
                    └─────────┬───────────┘──.delay──▶ Celery default queue
                              │
                    ┌─────────▼───────────┐
   Celery ─────────▶│  run_import_batch   │──bypass──▶ 读 batch / 写 failed job / 汇总
                    │  (双 session,NF-1)  │──app+SET LOCAL──▶ adapter.upsert (RLS)
                    └─────────┬───────────┘──get_object_bytes──▶ R2 [U01 helper]
                              │
                    ┌─────────▼───────────┐
                    │ ImportAdapterRegistry│──get(source)──▶ U06b/c/d/e Adapter
                    └─────────────────────┘                  (各自 repository 幂等 upsert)

   依赖：U01（R2 helper / Celery / 多租户 / 审计 / metrics）；不依赖 U05 Attachment ORM（FB-A）
```

---

## 4. 与 U06b/c/d/e 注册契约

```python
# U06b 示例：modules/importer/adapters/style_sku.py
class StyleSkuImportAdapter:
    source = "manual_style_sku"
    target_table = "style/sku"
    def parse_row(self, row, mapping): ...           # 列映射 + 类型转换
    def validate(self, parsed): ...                  # 业务校验
    async def upsert(self, parsed, *, session, tenant_id, actor_id):
        # 复用 U02 StyleRepository/SkuRepository.upsert_atomic（按 style_code/sku_code 幂等）
        ...
        return resource_id, is_inserted

def register() -> None:
    ImportAdapterRegistry.register(StyleSkuImportAdapter())
```

| 单元 | source | 目标 | 幂等键 | 复用 |
|---|---|---|---|---|
| U06b | manual_style_sku | style/sku | style_code/sku_code | U02 upsert_atomic |
| U06c | manual_blogger | blogger | xiaohongshu_id | U03 upsert_atomic |
| U06d | manual_promotion | promotion | internal_code | U04 |
| U06e | manual_settlement | settlement | settlement_no | U05 |

---

## 5. API 端点（U06a 框架）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/import/upload` | importer.batch:write | multipart 上传（DB 先行 + 异步触发） |
| GET | `/api/import/batches/` | importer.batch:read | 列表 + 过滤分页 |
| GET | `/api/import/batches/{id}` | importer.batch:read | 详情（含 imported/failed/status） |
| POST | `/api/import/batches/{id}/retry` | importer.batch:write | 重试（原子 claim + 两类分流） |
| GET | `/api/import/batches/{id}/errors/download` | importer.batch:read | 失败明细 CSV（csv_safe 转义） |
| POST | `/api/import/field-mappings` | importer.mapping:write | 新建映射版本（旧 active 下线） |
| GET | `/api/import/field-mappings` | importer.batch:read | 列版本 |
| GET | `/api/import/field-mappings/active?source=` | importer.batch:read | 当前生效版本 |

---

## 6. 一致性校验

| 校验 | 结果 |
|---|---|
| modules/importer 12 组件 + import_tasks + 7 横切修改 | ✅ §1 |
| NF-1 per-row SET LOCAL（import_tasks） | ✅ §1.2 |
| NF-2 DB 先行 + 补偿（service.upload） | ✅ §1.1 service |
| NF-3 原子 claim（repository.claim_for_retry） | ✅ §1.1 repository |
| NF-4 autodiscover import_tasks + worker 注册 | ✅ §1.3 celery_app + §2 |
| NF-5 importer.batch/mapping 权限 + default_roles | ✅ §1.1 permissions + §1.3 default_roles |
| NF-6 nginx client_max_body_size | ✅ §1.3 部署 |
| FB-A get_object_bytes（不碰 Attachment ORM） | ✅ §1.3 attachment |
| 与 U06b/c/d/e 注册契约清晰 | ✅ §4 |
| 启动序列 HTTP + worker 双加载 | ✅ §2 |
