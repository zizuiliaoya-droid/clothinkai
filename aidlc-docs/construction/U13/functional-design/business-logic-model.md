# U13 业务逻辑模型（Business Logic Model）

> 单元：U13 — 自动数据采集 Worker
> 故事：EP07-S11~S14

---

## 1. 用例列表

| UC | 名称 | 故事 | 角色 |
|---|---|---|---|
| UC-1 | 签发/吊销 Worker Token | §2.2.1 | 管理员 |
| UC-2 | Beat 调度生成采集任务 | EP07-S11~S13 | 系统 |
| UC-3 | Worker poll 领取任务 | EP07-S11~S13 | Worker |
| UC-4 | Worker exchange 换取凭据明文 | EP07-S04/§2.2.1 | Worker |
| UC-5 | Worker result 上报 → 触发导入 | EP07-S11~S13 | Worker |
| UC-6 | 3 平台 Adapter 入库 | EP07-S11~S13 | 系统 |
| UC-7 | 数据质量看板 | EP07-S14 | 管理员/运营 |

---

## 2. UC-1 签发/吊销 Worker Token

```
admin → POST /api/crawler/worker-tokens {name, ip_allowlist}
├─ 生成明文 token = token_urlsafe(32)
├─ 存 WorkerToken(token_hash=sha256(明文), ip_allowlist, is_active=true)
├─ audit worker_token.create
└─ 返回 {id, name, token=明文}（仅此一次）

admin → DELETE /api/crawler/worker-tokens/{id}（吊销）
└─ is_active=false + audit
```

---

## 3. UC-2 Beat 调度生成任务

```
Celery Beat 02:00 → schedule_daily_tasks（crawler 队列）
└─ 逐租户 system_context：
   ├─ 查 active 凭据（status=active）
   ├─ target_date = 昨天
   └─ 逐凭据 INSERT CrawlerTask(pending) ON CONFLICT(tenant,platform,credential_id,target_date) DO NOTHING
返回生成任务数
```

---

## 4. UC-3 Worker poll

```
Worker → POST /api/crawler/tasks/poll  (X-Worker-Token + IP)
├─ 鉴权 worker_token（BR-U13-02/03）；失败计数+告警（BR-U13-04）
├─ 原子领取一个 pending 任务：
│    UPDATE crawler_task SET status='assigned', worker_token_id=:wt,
│      assigned_at=now(), cred_token=:tok, cred_token_expires_at=now()+5min
│    WHERE id=(SELECT id FROM crawler_task WHERE tenant_id=:t AND status='pending'
│             ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *
├─ audit crawler.poll
└─ 返回 {task_id, platform, target_date, credential_id, cred_token, expires_at}（无明文密码）
   无任务 → 204
```

---

## 5. UC-4 Worker exchange

```
Worker → POST /api/crawler/tasks/{id}/exchange {cred_token}
├─ 校验 task.status='assigned' + cred_token 匹配 + 未过期（BR-U13-23/25）
├─ plaintext = CredentialService.decrypt_for_purpose(credential_id, "crawler_{platform}")
│    （内部写 credential.decrypt 审计 + 指标）
├─ task.cred_token=NULL（一次性）+ status='exchanged'
├─ audit crawler.exchange
└─ 返回 {username, password=plaintext}（仅此响应；不写日志）
```

---

## 6. UC-5 Worker result

```
Worker 登录平台采集 → 上传文件 → POST /api/crawler/tasks/{id}/result (multipart)
  {status, file?, error?}
├─ status=success:
│    ├─ batch = ImportService.upload_for_crawler(content, source=platform, tenant_id)
│    │    （actor_type=worker，触发 run_import_batch.delay）
│    ├─ task.import_batch_id = batch.id + status='success'
│    └─ CredentialService.report_success(credential_id)
├─ status=failed:
│    ├─ task.error_reason=error + status='failed'
│    └─ CredentialService.report_failure(credential_id, error)  ← 连续失败暂停+告警
├─ audit crawler.result
└─ 返回 {ok, batch_id?}
```

---

## 7. UC-6 Adapter 入库（异步 run_import_batch）

```
run_import_batch(batch_id) → ImportAdapterRegistry.get(source) → adapter
逐行：adapter.parse_row → validate → upsert
  QianniuAdapter.upsert:
    ├─ pp = PlatformProductService.find_by_platform_id("千牛", platform_id)
    ├─ pp 命中 → platform_product_id = pp.id
    │    未命中 → platform_product_id=NULL + DataQualityIssue(qianniu, warning, entity_ref=platform_id)
    └─ INSERT qianniu_daily ON CONFLICT(tenant,platform_id_snapshot,date) DO UPDATE（幂等）
  HuitunAdapter.upsert:
    ├─ blogger = 按 xiaohongshu_id 查
    │    未命中 → DataQualityIssue(huitun, warning)
    └─ blogger.audience_profile = 合并采集画像（U11 read_like_ratio 据此衍生）
```

---

## 8. UC-7 数据质量看板

```
admin/operations → GET /api/data-quality/summary
└─ SELECT source, severity, COUNT(*) GROUP BY source, severity（显式 tenant 过滤）

→ GET /api/data-quality/issues?source=&severity=&status= + 分页
→ PUT /api/data-quality/issues/{id} {status: fixed|ignored} + audit
```

---

## 9. J3 端到端采集时序

```
Beat ──── 系统 ──── Worker ──── 平台(千牛) ──── 导入 ──── 报表(U14)
 │02:00     │          │            │             │           │
 │ schedule │          │            │             │           │
 │─────────▶│ 建 task  │            │             │           │
 │          │◀── poll ─│            │             │           │
 │          │ cred_token│            │             │           │
 │          │◀exchange─│            │             │           │
 │          │ 明文密码 │            │             │           │
 │          │──────────▶│ 登录采集  │             │           │
 │          │◀── result(file) ──────│             │           │
 │          │ upload_for_crawler ─────────────────▶│           │
 │          │          │            │  run_import  │           │
 │          │          │            │  3 adapter   │           │
 │          │          │            │  qianniu_daily│──────────▶│ 聚合
```

---

## 10. CrawlerTaskService / WorkerTokenService / DataQualityService 接口

```python
class WorkerTokenService:
    async def issue(self, name, ip_allowlist, user) -> tuple[WorkerToken, str]  # 返回明文一次
    async def revoke(self, token_id, user) -> None
    async def authenticate(self, raw_token, client_ip) -> WorkerToken  # 鉴权+失败计数+自动吊销

class CrawlerTaskService:
    async def schedule_daily_tasks(self) -> int  # Beat
    async def poll_next_task(self, worker_token) -> CrawlerTaskAssignment | None
    async def exchange_credential(self, task_id, cred_token) -> CredentialPlaintext
    async def report_result(self, task_id, status, *, content=None, filename=None, error=None) -> dict

class DataQualityService:
    async def record(self, source, severity, message, *, entity_type=None, entity_ref=None) -> None
    async def summary(self, tenant_id) -> list[dict]  # source × severity 计数
    async def list_issues(self, *, filters, page, page_size) -> Page
    async def resolve(self, issue_id, status, user) -> None
```

---

## 11. 跨单元契约

| 方向 | 契约 |
|---|---|
| U13 → U12 | CredentialService.decrypt_for_purpose / report_failure / report_success |
| U13 → U10b | PlatformProductService.find_by_platform_id（adapter 反查） |
| U13 → U06a | ImportService.upload_for_crawler（新增系统 actor 封装）+ ImportAdapterRegistry 注册 3 adapter |
| U13 → U11 | HuitunAdapter 写 blogger.audience_profile（U11 read_like_ratio 消费） |
| U13 → U07 | NotificationService（worker_token 自动吊销告警 + 凭据失败告警复用 U12） |
| U14 → U13 | 读 qianniu_daily / ad_daily 聚合报表 |

---

## 12. 一致性校验

| 校验 | 结果 |
|---|---|
| 7 UC 覆盖 EP07-S11~S14 + §2.2.1 | ✅ |
| Worker poll/exchange/result 一次性 cred_token 流 | ✅ UC-3/4/5 |
| 3 adapter 反查 + 未匹配 issue | ✅ UC-6 |
| 数据质量看板 source×severity | ✅ UC-7 |
| 跨单元契约 U12/U10b/U06a/U11/U07/U14 明确 | ✅ §11 |
