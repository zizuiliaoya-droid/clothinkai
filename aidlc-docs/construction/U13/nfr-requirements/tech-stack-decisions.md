# U13 技术栈决策（Tech Stack Decisions）

> 单元：U13 — 自动数据采集 Worker
> 增量式：零新增依赖，复用 U01/U06a/U07/U12

---

## 1. 依赖决策

| 能力 | 选型 | 状态 |
|---|---|---|
| worker_token 生成 | secrets.token_urlsafe + hashlib.sha256 | 标准库 |
| 凭据解密 | U12 CredentialService.decrypt_for_purpose | 复用 |
| 平台商品反查 | U10b PlatformProductService.find_by_platform_id | 复用 |
| 导入触发 | U06a ImportService + ImportAdapterRegistry + run_import_batch | 复用 |
| 通知 | U07 NotificationService | 复用 |
| Celery | crawler 队列 + Beat schedule_daily_tasks | 复用 celery_app |

> **结论：requirements.txt 不变。**

---

## 2. 代码落点

### modules/collect/（新建）

| 文件 | 职责 |
|---|---|
| `__init__.py` / `enums.py` | CrawlerStatus / DqSeverity / DqStatus |
| `config.py` | WORKER_AUTH_FAILURE_THRESHOLD=5 / CRED_TOKEN_TTL_SECONDS=300 |
| `exceptions.py` | WorkerTokenInvalid(401)/WorkerIpForbidden(403)/CredTokenInvalid(403)/CrawlerTaskNotFound(404)/DqIssueNotFound(404) |
| `permissions.py` | crawler.worker:write / crawler.task:read / data_quality:read|write |
| `models.py` | WorkerToken / CrawlerTask / DataQualityIssue / QianniuDaily / AdDaily |
| `schemas.py` | WorkerTokenCreate/Public / CrawlerTaskAssignment / CredExchangeResponse / DqIssue/DqSummary |
| `repository.py` | CrawlerTaskRepository / WorkerTokenRepository / DataQualityRepository |
| `worker_token_service.py` | WorkerTokenService（issue/revoke/authenticate） |
| `crawler_task_service.py` | CrawlerTaskService（schedule/poll/exchange/report_result） |
| `data_quality_service.py` | DataQualityService（record/summary/list/resolve） |
| `deps.py` | service deps + WorkerTokenAuth 依赖 |
| `crawler_api.py` | Worker API /api/crawler/*（worker_token 鉴权） |
| `worker_token_api.py` | 管理员 /api/crawler/worker-tokens |
| `data_quality_api.py` | /api/data-quality/* |

### modules/importer/adapters/（追加 3 adapter）

| 文件 | source → 目标 |
|---|---|
| `qianniu.py` | qianniu → qianniu_daily（find_by_platform_id 反查） |
| `wanxiangtai.py` | wanxiangtai → ad_daily |
| `huitun.py` | huitun → blogger.audience_profile |

### 修改

| 组件 | 改动 |
|---|---|
| `modules/importer/service.py` | +upload_for_crawler(content, source, tenant_id)（系统 actor 封装） |
| `modules/importer/registry.py` 调用方（main lifespan + worker_process_init） | 注册 3 新 adapter |
| `core/celery_app.py` | autodiscover +tasks.crawler_tasks + crawler 队列 + Beat schedule_daily_tasks 02:00 |
| `tasks/crawler_tasks.py` | schedule_daily_tasks Celery 任务（新建） |
| `core/metrics.py` | +4 指标 |
| `app/main.py` | 注册 crawler_router + worker_token_router + data_quality_router + 注册 3 adapter |
| `alembic/versions/017_u13_create_crawler_tables.py` | 5 表 + RLS + scope seed |

---

## 3. WorkerToken 鉴权依赖

```python
# modules/collect/deps.py
async def get_worker_token(
    session: SessionDep,
    request: Request,
    x_worker_token: Annotated[str | None, Header()] = None,
) -> WorkerToken:
    if not x_worker_token:
        raise WorkerTokenInvalid()
    client_ip = request.client.host
    return await WorkerTokenService(session).authenticate(x_worker_token, client_ip)

WorkerTokenDep = Annotated[WorkerToken, Depends(get_worker_token)]
```

---

## 4. cred_token 生成 / exchange

```python
import secrets, hashlib

def _new_cred_token() -> str:
    return secrets.token_urlsafe(32)

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
```

---

## 5. 指标（core/metrics.py 追加）

```python
crawler_task_total = Counter("crawler_task_total", "...", ["platform", "status"])
crawler_poll_total = Counter("crawler_poll_total", "...", ["result"])
worker_token_auth_failures_total = Counter("worker_token_auth_failures_total", "...")
data_quality_issue_total = Counter("data_quality_issue_total", "...", ["source", "severity"])
```

---

## 6. Celery 队列 + Beat

```python
# celery_app.py
task_queues += {"crawler": {}}
autodiscover += ["app.tasks.crawler_tasks"]
beat_schedule["crawler-daily-collect"] = {
    "task": "app.tasks.crawler_tasks.schedule_daily_tasks",
    "schedule": crontab(hour=2, minute=0),
    "options": {"queue": "crawler"},
}
```

---

## 7. ImportService.upload_for_crawler

```python
async def upload_for_crawler(
    self, *, content: bytes, source: str, tenant_id: UUID,
    filename: str, content_type: str = "text/csv",
) -> ImportBatch:
    """Worker 采集结果导入（系统 actor，复用 upload 主体；actor_id=None，audit actor_type=worker）。"""
```

---

## 8. 测试落点

| 文件 | 类型 |
|---|---|
| tests/unit/test_crawler_adapters.py | 3 adapter parse/validate/upsert + 反查未匹配 issue |
| tests/unit/test_worker_token.py | cred_token 生成/hash + authenticate 失败计数 |
| tests/integration/test_crawler_flow.py | 调度 + poll + exchange + result→upload + 失败联动 + RLS |
| tests/api/test_crawler_api.py | Worker 鉴权矩阵 + data-quality 看板 + OpenAPI |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增依赖 | ✅ |
| modules/collect + 3 adapter 落点 | ✅ |
| upload_for_crawler 系统 actor | ✅ |
| WorkerToken 鉴权依赖 | ✅ |
| 4 metrics + crawler 队列 + Beat | ✅ |
| migration 017 5 表 | ✅ |
| 测试 4 文件 | ✅ |
| 与 nfr-requirements 一致 | ✅ |
