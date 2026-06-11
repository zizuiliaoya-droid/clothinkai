# U06a 统一导入框架 — 代码交付摘要

> 单元：U06a — 统一导入框架（导入并行支线，依赖仅 U01）
> 状态：Code Generation 完成（Batch 1-3 / 10 Step 全部交付）
> 验证：migration 010 全链路 `alembic upgrade head` 成功；importer 61 测试全绿；
> 全量 494 passed / 0 failed，覆盖率 77.89% ≥ 70%

---

## 1. 单元职责

U06a 是**框架层**，提供统一文件导入能力，不含具体业务 Adapter（那些由 U06b/c/d/e 实现并注册）：

- 通用上传 API（CSV / XLSX）+ file_hash 去重
- `import_batch` / `import_job` / `field_mapping` 三张表
- 异步解析编排（`run_import_batch` Celery 任务）
- `ImportAdapter` 协议 + `ImportAdapterRegistry` 注册中心
- 字段映射版本管理 + 失败明细下载 + 两类失败重试

---

## 2. 文件清单

### 2.1 模块代码（backend/app/modules/importer/）
| 文件 | 职责 |
|---|---|
| `enums.py` | ImportBatchStatus(4) / ImportJobStatus(2) |
| `permissions.py` | importer.batch:read/write + importer.mapping:write（NF-5） |
| `exceptions.py` | 12 业务异常（422/409/404/500 + 行级） |
| `models.py` | ImportBatch / ImportJob / FieldMapping（4 UNIQUE + CHECK） |
| `schemas.py` | Pydantic 响应/分页/过滤/映射创建 |
| `adapter.py` | ImportAdapter Protocol（FB-C 签名） |
| `registry.py` | ImportAdapterRegistry（进程内注册中心） |
| `domain.py` | csv_safe + compute_sha256 + safe_filename + validate_mapping_config（纯函数） |
| `repository.py` | 3 Repository（含 claim_for_retry NF-3） |
| `field_mapping_service.py` | 映射版本管理（旧 active 同事务下线） |
| `service.py` | ImportService（upload DB 先行 NF-2 + retry NF-3 + download csv_safe） |
| `deps.py` | ImportServiceDep / FieldMappingServiceDep |
| `api.py` | 8 REST 端点 |

### 2.2 任务 / 横切（backend/app/）
| 文件 | 变更 |
|---|---|
| `tasks/import_tasks.py` | **新建** — run_import_batch（per-row SET LOCAL NF-1 + 双 session + worker_process_init NF-4） |
| `core/metrics.py` | +5 导入指标 |
| `core/config.py` | +4 IMPORT_* 配置 |
| `core/attachment.py` | +get_object_bytes（FB-A，U01 R2 helper 扩展） |
| `core/celery_app.py` | autodiscover +import_tasks（NF-4） |
| `modules/auth/default_roles.py` | pr/pr_manager += importer.batch/mapping（NF-5） |
| `main.py` | register_import_adapters + import_router |

### 2.3 迁移 / 测试 / 前端 / CI
| 文件 | 内容 |
|---|---|
| `alembic/versions/010_u06a_create_import_tables.py` | 3 表 + 4 UNIQUE + 索引 + 3 RLS + permission seed |
| `tests/{unit,integration,api}/test_import_*.py` | 10 测试文件，61 用例 |
| `tests/conftest.py` | +FakeImportAdapter + import_batch_factory + operations_role |
| `frontend/src/features/import/{types,api}.ts` | 前端类型 + API 调用层 |
| `.github/workflows/ci.yml` | +validate-import-framework job（NF-4 grep） |
| `frontend/nginx.conf` | client_max_body_size 21m（NF-6 L1） |
| `requirements.txt` / `requirements-dev.txt` | +openpyxl==3.1.5 / +freezegun==1.5.1 |

---

## 3. 关键架构决策（11 反馈守护，全部落地）

| 反馈 | 落地点 |
|---|---|
| FB-A 不用 Attachment ORM | service.upload_bytes / runner.get_object_bytes（U01 R2 helper） |
| FB-C Adapter 契约 | adapter.upsert(session, tenant_id, actor_id)；runner 持有 per-row 事务 |
| FB-D upload→processing | 无 pending 状态 + UNIQUE(tenant,source,file_hash) |
| FB-E retry 两类分流 | partial→only_failed 原地更新；failed→整文件；ON CONFLICT attempt_count+1 |
| **NF-1** per-row SET LOCAL | `SELECT set_config('app.tenant_id', :tid, true)`（事务级，防连接池串租） |
| **NF-2** DB 先行 + 补偿 | SAVEPOINT 内 INSERT→UNIQUE 原子→R2 写；任一失败回滚 savepoint（无孤儿） |
| **NF-3** 原子 claim | claim_for_retry UPDATE WHERE status IN(partial,failed) RETURNING |
| **NF-4** 双进程注册 | celery autodiscover + worker_process_init 注册 Adapter |
| **NF-5** 权限对齐 | importer.batch/mapping + migration seed + default_roles |
| **NF-6** 三层 body 上限 | nginx 21m（L1）+ handler 分块读取（L2）+ 解析行数（L3） |

---

## 4. Build & Test 修复（本批真实跑测发现）

| 问题 | 根因 | 修复 |
|---|---|---|
| NF-1 SET LOCAL 失败 | asyncpg 的 `SET LOCAL x = $1` 语法不支持占位符（PostgresSyntaxError） | 改 `SELECT set_config('app.tenant_id', :tid, true)`（等价 SET LOCAL，接受 bind 参数） |
| upload 409/补偿 测试失败 | `session.rollback()` 丢弃整个事务 + commit 后 ORM 懒加载 user.tenant_id（MissingGreenlet） | 改 SAVEPOINT（`begin_nested`）+ 上传前捕获 tenant_id/actor_id 为本地变量 |
| 映射 v1 下线断言失败 | 批量 UPDATE（synchronize_session=False）后 identity-map 仍持旧 is_active | get_active/get_by_version 加 `populate_existing=True` |

> SAVEPOINT 方案同时更**生产正确**：upload 失败仅回滚导入相关写入，不波及请求事务其余部分。
