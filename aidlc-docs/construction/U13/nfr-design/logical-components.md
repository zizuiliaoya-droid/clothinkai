# U13 逻辑组件（Logical Components）

> 单元：U13 — 自动数据采集 Worker
> 新建 modules/collect 14 文件 + 3 adapter + 横切 5 改动 + tasks + migration 017

---

## 1. 新建组件（modules/collect/）

| 文件 | 职责 |
|---|---|
| `__init__.py` | 模块包 |
| `enums.py` | CrawlerStatus(pending/assigned/exchanged/success/failed) + DqSeverity(info/warning/error) + DqStatus(open/fixed/ignored) + CrawlerPlatform |
| `config.py` | WORKER_AUTH_FAILURE_THRESHOLD=5 + CRED_TOKEN_TTL_SECONDS=300 |
| `exceptions.py` | WorkerTokenInvalid(401)/WorkerIpForbidden(403)/CredTokenInvalid(403)/CrawlerTaskNotFound(404)/DqIssueNotFound(404) |
| `permissions.py` | crawler.worker:write / crawler.task:read / data_quality:read|write |
| `models.py` | WorkerToken / CrawlerTask / DataQualityIssue / QianniuDaily / AdDaily |
| `schemas.py` | WorkerTokenCreate/Public/Issued + CrawlerTaskAssignment + CredExchangeRequest/Response + CrawlerResultIn + DqIssue/DqSummary/DqPage |
| `repository.py` | WorkerTokenRepository / CrawlerTaskRepository / DataQualityRepository |
| `worker_token_service.py` | WorkerTokenService（issue/revoke/authenticate） |
| `crawler_task_service.py` | CrawlerTaskService（schedule_daily_tasks/poll_next_task/exchange_credential/report_result） |
| `data_quality_service.py` | DataQualityService（record/summary/list_issues/resolve） |
| `deps.py` | WorkerTokenDep + 3 service deps |
| `crawler_api.py` | Worker API /api/crawler/tasks/poll|{id}/exchange|{id}/result（worker_token 鉴权） |
| `worker_token_api.py` + `data_quality_api.py` | 管理端 /api/crawler/worker-tokens + /api/data-quality/* |

## 2. 新建 adapter（modules/importer/adapters/）

| 文件 | source → 目标 |
|---|---|
| `qianniu.py` | qianniu → qianniu_daily（find_by_platform_id 反查） |
| `wanxiangtai.py` | wanxiangtai → ad_daily |
| `huitun.py` | huitun → blogger.audience_profile |

## 3. 新建 task

| 文件 | 职责 |
|---|---|
| `tasks/crawler_tasks.py` | schedule_daily_tasks（Beat 02:00，crawler 队列，逐租户容错） |

## 4. 修改组件

| 组件 | 改动 |
|---|---|
| `modules/importer/service.py` | 抽 `_create_batch` + 新增 `upload_for_crawler`（系统 actor） |
| 注册 adapter 调用方（main lifespan + celery worker_process_init） | 注册 qianniu/wanxiangtai/huitun 3 adapter |
| `core/celery_app.py` | autodiscover +tasks.crawler_tasks + crawler 队列 + Beat schedule_daily_tasks 02:00 |
| `core/metrics.py` | +4 指标（crawler_task_total/crawler_poll_total/worker_token_auth_failures_total/data_quality_issue_total） |
| `app/main.py` | 注册 crawler_router + worker_token_router + data_quality_router |
| `alembic/versions/017_u13_create_crawler_tables.py` | 5 表 + RLS + UNIQUE + idx + 4 scope seed |

## 5. 复用组件

| 复用 | 来源 |
|---|---|
| CredentialService.decrypt_for_purpose / report_failure / report_success | U12 |
| PlatformProductService.find_by_platform_id | U10b |
| ImportService（upload 主体）+ ImportAdapterRegistry + run_import_batch | U06a |
| NotificationService（worker_token 吊销告警） | U07 |
| AuditService + TenantScopedModel + RLS + system_context | U01 |

## 6. 依赖图

```
crawler_api (Worker) ──WorkerTokenDep──▶ WorkerTokenService
  → CrawlerTaskService
      → CredentialService.decrypt_for_purpose (U12)
      → ImportService.upload_for_crawler (U06a)
      → CredentialService.report_failure/success (U12)
worker_token_api / data_quality_api (admin) ──require_permission──▶ 对应 service

tasks/crawler_tasks.schedule_daily_tasks
  → CrawlerTaskService.schedule (system_context 逐租户)

run_import_batch (U06a) → ImportAdapterRegistry.get(qianniu/wanxiangtai/huitun)
  → QianniuAdapter → PlatformProductService.find_by_platform_id (U10b) + DataQualityService.record
  → HuitunAdapter → blogger.audience_profile (U11)
```
- 无循环依赖：collect 单向依赖 U12/U10b/U06a/U07/U01；adapter 单向依赖 U10b/collect(DataQualityService)。

## 7. migration 017

```text
5 表（TenantScopedModel + RLS）：
- worker_token：UNIQUE(tenant,token_hash) + idx(tenant,is_active)
- crawler_task：UNIQUE(tenant,platform,credential_id,target_date) + idx(tenant,status)
                + FK credential CASCADE + FK worker_token SET NULL
- data_quality_issue：idx(tenant,source,severity) + idx(tenant,status) + 2 CHECK
- qianniu_daily：UNIQUE(tenant,platform_id_snapshot,date) + FK platform_product SET NULL + idx(tenant,date)
- ad_daily：UNIQUE(tenant,platform_id_snapshot,date) + FK platform_product SET NULL + idx(tenant,date)
scope seed：crawler.worker:write/crawler.task:read → admin；data_quality:read → admin/operations；data_quality:write → admin
downgrade：DROP 5 表 + DELETE scope
```

## 8. 测试文件

| 文件 | 类型 |
|---|---|
| tests/unit/test_crawler_adapters.py | 3 adapter parse/validate/upsert + 反查未匹配 issue |
| tests/unit/test_worker_token.py | hash/authenticate 失败计数自动吊销 + cred_token |
| tests/integration/test_crawler_flow.py | 调度 + poll SKIP LOCKED + exchange 一次性 + result→upload + 失败联动 + RLS |
| tests/api/test_crawler_api.py | Worker 鉴权矩阵 6 场景 + data-quality 看板 + OpenAPI |

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 新建 14 + 3 adapter + 1 task + 修改 6 + migration 017 | ✅ |
| 复用 U12/U10b/U06a/U07/U01 | ✅ |
| 无循环依赖 | ✅ |
| 与 P-U13-01/02/03 一致 | ✅ |
