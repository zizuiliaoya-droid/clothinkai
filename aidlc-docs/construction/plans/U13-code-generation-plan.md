# U13 代码生成计划（Code Generation Plan）

> 单元：U13 — 自动数据采集 Worker（EP07-S11~S14）
> 分批：**4 批** + Build & Test（项目最大单元）
> Build & Test：Docker PG16:5556 + Redis7:6411 + Py3.12

---

## 0. 澄清回答（预填 [Answer]）

- [Answer] 新建 `modules/collect/`（14 文件）+ 3 adapter 放 `modules/importer/adapters/`。
- [Answer] WorkerToken 鉴权用 `X-Worker-Token` header（sha256），独立 deps（不走 JWT）。
- [Answer] crawler_task poll 用 raw SQL `FOR UPDATE SKIP LOCKED`；cred_token=secrets.token_urlsafe(32)，TTL 300s。
- [Answer] 3 adapter 实现 ImportAdapter 协议 + register()；register_import_adapters 追加 3 模块。
- [Answer] ImportService 抽 upload_for_crawler（actor_id=None，system_context 提供 tenant）。
- [Answer] tasks/crawler_tasks.schedule_daily_tasks 逐租户 system_context + AsyncSessionBypass 读 tenant。
- [Answer] migration 017：5 表 + RLS + UNIQUE + idx + 4 scope seed（admin/operations）。
- [Answer] Build & Test Docker 5556/6411。

---

## 1. 步骤（4 批）

### Batch 1 — 模块基础 + 模型 + Schema
- [x] 1.1 modules/collect/__init__.py + enums.py（CrawlerStatus/DqSeverity/DqStatus/CrawlerPlatform）+ config.py（WORKER_AUTH_FAILURE_THRESHOLD=5/CRED_TOKEN_TTL_SECONDS=300）
- [x] 1.2 modules/collect/exceptions.py（5 异常）+ permissions.py（4 scope）
- [x] 1.3 modules/collect/models.py（WorkerToken/CrawlerTask/DataQualityIssue/QianniuDaily/AdDaily）
- [x] 1.4 modules/collect/schemas.py（WorkerToken*/CrawlerTaskAssignment/CredExchange*/CrawlerResultIn/Dq*）

### Batch 2 — Repository + Service + Deps
- [x] 2.1 modules/collect/repository.py（WorkerTokenRepository/CrawlerTaskRepository/DataQualityRepository）
- [x] 2.2 modules/collect/worker_token_service.py（issue/revoke/authenticate 失败计数自动吊销）
- [x] 2.3 modules/collect/data_quality_service.py（record/summary/list/resolve）
- [x] 2.4 modules/collect/crawler_task_service.py（schedule/poll SKIP LOCKED/exchange 一次性/report_result）
- [x] 2.5 modules/collect/deps.py（WorkerTokenDep + 3 service deps）

### Batch 3 — API + Adapter + 横切
- [x] 3.1 modules/collect/crawler_api.py（poll/exchange/result，worker_token 鉴权）
- [x] 3.2 modules/collect/worker_token_api.py + data_quality_api.py（管理端）
- [x] 3.3 modules/importer/adapters/qianniu.py + wanxiangtai.py + huitun.py（实现 ImportAdapter + register）
- [x] 3.4 modules/importer/service.py 追加 upload_for_crawler（抽 _create_batch 复用）
- [x] 3.5 tasks/crawler_tasks.py（schedule_daily_tasks 逐租户容错）
- [x] 3.6 横切：core/metrics +4 指标 + celery_app（crawler 队列+autodiscover+Beat）+ main（注册 3 router + register_import_adapters 追加 3 模块）

### Batch 4 — migration + 测试
- [x] 4.1 alembic/versions/017_u13_create_crawler_tables.py（5 表 + RLS + 4 scope seed）
- [x] 4.2 tests/unit/test_crawler_adapters.py + test_worker_token.py
- [x] 4.3 tests/integration/test_crawler_flow.py
- [x] 4.4 tests/api/test_crawler_api.py

### Build & Test
- [x] B.1 Docker PG16:5556 + Redis7:6411；alembic upgrade head（含 017）；U13 子集 + 全量回归；覆盖率 ≥70%

---

**本轮执行全部 4 批 + Build & Test。**
