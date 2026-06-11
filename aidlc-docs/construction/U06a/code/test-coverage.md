# U06a 测试覆盖摘要

> importer 模块 61 用例全绿；全量套件 494 passed / 0 failed；总覆盖率 77.89% ≥ 70%
> 真实环境：Docker python:3.12-slim + PostgreSQL 16 + Redis 7（匹配 CI）

---

## 1. 测试文件清单（10 文件，61 用例）

| 文件 | 层 | 用例数 | 覆盖点 |
|---|---|---|---|
| `unit/test_import_domain.py` | unit | 18 | csv_safe / compute_sha256 / safe_filename / validate_mapping_config |
| `unit/test_import_state_machine.py` | unit | 3 | ImportBatchStatus 4 值（无 pending）/ ImportJobStatus 2 值 |
| `unit/test_import_registry.py` | unit | 5 | register/get/sources/clear/幂等覆盖 |
| `integration/test_import_upload.py` | integration | 5 | processing 创建 / source 422 / 格式 422 / **去重 409 NF-2** / **R2 补偿 NF-2** |
| `integration/test_import_field_mapping.py` | integration | 3 | v1 active / **v2 下线 v1** / 版本倒序 |
| `integration/test_import_retry.py` | integration | 6 | **partial→only_failed** / **failed→整文件** / 耗尽 409 / busy 409 / 404 / **claim 原子互斥 NF-3** |
| `integration/test_import_errors_download.py` | integration | 3 | **csv_safe 转义** / 正常值保真 / 仅 failed 行 |
| `integration/test_import_runner.py` | integration | 6 | parse CSV/XLSX/BOM / sanitize / **端到端 partial** / adapter 缺失 |
| `integration/test_import_tenant_isolation.py` | integration | 1 | **runner per-row tenant 一致 NF-1** |
| `api/test_import_api.py` | api | 8 | 6 端点鉴权 401 + OpenAPI 8 端点暴露 |

---

## 2. 故事追溯（EP07-S07~S10）

| 故事 | 实施 | 守护测试 |
|---|---|---|
| EP07-S07 上传 | service.upload + run_import_batch | test_import_upload + test_import_runner |
| EP07-S08 hash 去重 | UNIQUE + IntegrityError→409 | test_import_upload::test_duplicate_file_409 |
| EP07-S09 映射版本 | field_mapping_service.create_version | test_import_field_mapping |
| EP07-S10 失败下载/重试 | download_errors + retry | test_import_errors_download + test_import_retry |

---

## 3. 反馈守护测试映射（11 条）

| 反馈 | 守护测试 |
|---|---|
| FB-A 不碰 Attachment ORM | test_import_runner（mock get_object_bytes） |
| FB-C Adapter 契约 | test_import_runner（FakeAdapter.upsert session/tenant/actor 签名） |
| FB-D upload→processing | test_import_upload::test_upload_creates_processing_batch |
| FB-E 两类分流 | test_import_retry::test_retry_partial/failed_* |
| NF-1 per-row SET LOCAL | test_import_tenant_isolation（upsert 记录 tenant_id == batch.tenant_id） |
| NF-2 DB 先行 + 补偿 | test_import_upload::test_duplicate_file_409 + test_upload_r2_failure_compensates |
| NF-3 原子 claim | test_import_retry::test_claim_for_retry_atomic_single_winner + busy/exhausted 409 |
| NF-4 autodiscover + worker 注册 | ci.yml grep（validate-import-framework）+ test_import_runner |
| NF-5 权限 | test_import_api 鉴权 + migration 010 seed |
| NF-6 三层 body 上限 | test_import_upload::test_upload_bad_format/file_too_large + nginx 配置 |
| CSV injection | test_import_errors_download::test_csv_safe_escapes_formula |

---

## 4. 模块覆盖率（关键文件）

| 文件 | 覆盖率 |
|---|---|
| enums / models / schemas / exceptions / registry | 100% |
| domain.py | 97% |
| field_mapping_service.py | 95% |
| service.py | 90% |
| adapter.py | 80%（Protocol stub 方法体不计） |
| repository.py | 59%（list/部分查询路径由 U06b 业务 Adapter 集成时补全） |
| api.py | 47%（端点装配；契约层鉴权/OpenAPI 已覆盖，正向流由 service 层测试覆盖） |

> runner（import_tasks.py）端到端通过真实 Celery 任务体在 test_import_runner 验证；
> 其行级事务/SET LOCAL/汇总路径在 partial 端到端用例中被实际执行。

---

## 5. 真实环境验证记录

- `alembic upgrade head`：001→010 全链路成功（migration 010 在 009 之上干净 apply）
- importer 子集：`61 passed`
- 全量：`494 passed / 0 failed`，coverage `77.89%`
- 命令：`pytest -m "not rls and not performance"`（与 CI 一致）
- 发现并修复 3 个真实/测试问题（详见 README §4）：asyncpg SET LOCAL 参数化 / SAVEPOINT 上传 / identity-map 刷新
