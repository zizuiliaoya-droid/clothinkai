# U13 NFR 设计模式（NFR Design Patterns）

> 单元：U13 — 自动数据采集 Worker
> 模式：P-U13-01（Worker 鉴权）、P-U13-02（调度+poll+exchange）、P-U13-03（result→import+adapter+data quality）

---

## P-U13-01 — Worker Token 鉴权 + IP + 自动吊销

```python
import hashlib

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class WorkerTokenService:
    async def authenticate(self, raw_token: str, client_ip: str) -> WorkerToken:
        wt = await self._repo.get_active_by_hash(_hash_token(raw_token))
        if wt is None:
            worker_token_auth_failures_total.inc()
            raise WorkerTokenInvalid()                       # BR-U13-02 → 401
        # IP allowlist（空 allowlist 视为拒绝）
        if client_ip not in (wt.ip_allowlist or []):
            await self._register_failure(wt, "ip_mismatch")  # BR-U13-03/04
            raise WorkerIpForbidden()                        # → 403
        # 成功：重置计数 + last_seen
        wt.consecutive_auth_failures = 0
        wt.last_seen_at = datetime.now(UTC)
        await self._session.commit()
        return wt

    async def _register_failure(self, wt: WorkerToken, reason: str) -> None:
        worker_token_auth_failures_total.inc()
        wt.consecutive_auth_failures += 1
        if wt.consecutive_auth_failures >= WORKER_AUTH_FAILURE_THRESHOLD:  # 5
            wt.is_active = False                             # 自动吊销
            await self._notify_admins_revoked(wt)            # 企微告警
        await self._session.commit()

    async def issue(self, name, ip_allowlist, user) -> tuple[WorkerToken, str]:
        raw = secrets.token_urlsafe(32)
        wt = WorkerToken(name=name, token_hash=_hash_token(raw),
                         ip_allowlist=list(ip_allowlist), is_active=True)
        self._repo.add(wt); await self._session.flush()
        await self._audit.log(action="worker_token.create", resource="worker_token",
                              resource_id=wt.id, after={"name": name}, user_id=user.id)
        await self._session.commit()
        return wt, raw                                       # 明文一次性返回
```

---

## P-U13-02 — 调度 + poll SKIP LOCKED + exchange 一次性 cred_token

### schedule_daily_tasks（Beat，逐租户容错）

```python
async def _schedule_impl():
    yesterday = get_today() - timedelta(days=1)
    async with AsyncSessionBypass() as meta:
        tenant_ids = (await meta.execute(text("SELECT id FROM tenant"))).scalars().all()
    total = 0
    for tid in tenant_ids:
        tok = tenant_id_ctx.set(tid)
        try:
            async with system_context():
                async with AsyncSessionApp() as s:
                    await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
                    creds = await CredentialRepository(s).list_active(tid)  # status=active
                    for c in creds:
                        await s.execute(text(
                            "INSERT INTO crawler_task (id, tenant_id, platform, credential_id, "
                            "target_date, status, attempt, created_at, updated_at) "
                            "VALUES (:id,:t,:p,:c,:d,'pending',0,NOW(),NOW()) "
                            "ON CONFLICT (tenant_id, platform, credential_id, target_date) DO NOTHING"
                        ), {...})
                        total += 1
                    await s.commit()
        except Exception as exc:
            log.exception("schedule_tenant_failed"); sentry_sdk.capture_exception(exc)
        finally:
            tenant_id_ctx.reset(tok)
    return {"scheduled": total}
```

### poll_next_task（FOR UPDATE SKIP LOCKED 原子领取）

```python
async def poll_next_task(self, wt: WorkerToken) -> CrawlerTaskAssignment | None:
    cred_token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + timedelta(seconds=CRED_TOKEN_TTL_SECONDS)  # 300
    row = (await self._session.execute(text(
        "UPDATE crawler_task SET status='assigned', worker_token_id=:wt, "
        "assigned_at=NOW(), cred_token=:ct, cred_token_expires_at=:exp, updated_at=NOW() "
        "WHERE id = (SELECT id FROM crawler_task WHERE tenant_id=:t AND status='pending' "
        "            ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) "
        "RETURNING id, platform, credential_id, target_date"
    ), {...})).first()
    await self._session.commit()
    if row is None:
        crawler_poll_total.labels("empty").inc()
        return None
    await self._audit.log(action="crawler.poll", resource="crawler_task",
                          resource_id=row.id, after={"worker_token_id": str(wt.id)})
    await self._session.commit()
    crawler_poll_total.labels("assigned").inc()
    return CrawlerTaskAssignment(task_id=row.id, platform=row.platform,
                                 credential_id=row.credential_id, target_date=row.target_date,
                                 cred_token=cred_token, expires_at=expires)  # 无明文密码
```

### exchange_credential（一次性 + TTL）

```python
async def exchange_credential(self, task_id, cred_token: str) -> CredExchangeResponse:
    task = await self._repo.get(task_id)
    if task is None:
        raise CrawlerTaskNotFound()
    if (task.status != "assigned" or task.cred_token != cred_token
            or task.cred_token_expires_at < datetime.now(UTC)):
        raise CredTokenInvalid()                              # BR-U13-23/24/25 → 403
    plaintext = await self._cred.decrypt_for_purpose(
        task.credential_id, purpose=f"crawler_{task.platform}")  # U12 写审计+指标
    task.cred_token = None                                    # 一次性清空
    task.status = "exchanged"
    await self._session.flush()
    await self._audit.log(action="crawler.exchange", resource="crawler_task",
                          resource_id=task.id)
    await self._session.commit()
    cred = await self._cred._require(task.credential_id)
    return CredExchangeResponse(username=cred.username, password=plaintext)  # 不写日志
```

---

## P-U13-03 — result → upload + 3 adapter 反查入库 + data quality

### report_result

```python
async def report_result(self, task_id, status, *, content=None, filename=None, error=None):
    task = await self._repo.get(task_id)
    if task is None:
        raise CrawlerTaskNotFound()
    if status == "success":
        batch = await self._import.upload_for_crawler(
            content=content, source=task.platform_source, tenant_id=task.tenant_id,
            filename=filename or f"{task.platform}_{task.target_date}.csv",
        )                                                     # 独立 commit + run_import_batch.delay
        task.import_batch_id = batch.id
        task.status = "success"
        await self._cred.report_success(task.credential_id)   # 重置失败计数
        crawler_task_total.labels(task.platform, "success").inc()
    else:
        task.error_reason = error
        task.status = "failed"
        await self._cred.report_failure(task.credential_id, error or "采集失败")  # 连续失败暂停+告警
        crawler_task_total.labels(task.platform, "failed").inc()
    await self._session.flush()
    await self._audit.log(action="crawler.result", resource="crawler_task",
                          resource_id=task.id, after={"status": status})
    await self._session.commit()
    return {"ok": True, "batch_id": str(task.import_batch_id) if task.import_batch_id else None}
```

### QianniuAdapter.upsert（反查 + 未匹配 record + UNIQUE upsert）

```python
class QianniuAdapter:
    source = "qianniu"; target_table = "qianniu_daily"

    async def upsert(self, parsed, *, session, tenant_id, actor_id):
        pp = await PlatformProductService(session).find_by_platform_id("千牛", parsed["platform_id"])
        ppid = pp.id if pp else None
        if pp is None:
            await DataQualityService(session).record(
                source="qianniu", severity="warning",
                message=f"未匹配 platform_product: {parsed['platform_id']}",
                entity_type="platform_product", entity_ref=parsed["platform_id"],
            )                                                  # 不阻塞入库
        await session.execute(text(
            "INSERT INTO qianniu_daily (id, tenant_id, platform_product_id, platform_id_snapshot, "
            "date, visitors, pay_amount, pay_orders, extra, created_at, updated_at) "
            "VALUES (:id,:t,:ppid,:pid,:d,:v,:amt,:ord,:ex,NOW(),NOW()) "
            "ON CONFLICT (tenant_id, platform_id_snapshot, date) DO UPDATE SET "
            "visitors=EXCLUDED.visitors, pay_amount=EXCLUDED.pay_amount, "
            "pay_orders=EXCLUDED.pay_orders, platform_product_id=EXCLUDED.platform_product_id, "
            "updated_at=NOW()"
        ), {...})                                              # 幂等；不自行 commit（runner 控制）
        return uuid4(), True
```

### HuitunAdapter.upsert（更新 blogger.audience_profile）

```python
async def upsert(self, parsed, *, session, tenant_id, actor_id):
    blogger = (await session.execute(select(Blogger).where(
        Blogger.xiaohongshu_id == parsed["xiaohongshu_id"]))).scalar_one_or_none()
    if blogger is None:
        await DataQualityService(session).record(source="huitun", severity="warning",
            message=f"未匹配 blogger: {parsed['xiaohongshu_id']}",
            entity_type="blogger", entity_ref=parsed["xiaohongshu_id"])
        return uuid4(), False
    blogger.audience_profile = parsed["audience_profile"]  # U11 read_like_ratio 据此衍生
    return blogger.id, False
```

### DataQualityService.summary（source × severity）

```python
async def summary(self, tenant_id) -> list[dict]:
    rows = await self._session.execute(text(
        "SELECT source, severity, COUNT(*) AS cnt FROM data_quality_issue "
        "WHERE tenant_id=:t GROUP BY source, severity"), {"t": str(tenant_id)})
    return [{"source": r.source, "severity": r.severity, "count": r.cnt} for r in rows]
```

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| worker_token sha256 + IP allowlist + 5 次失败自动吊销 | ✅ P-U13-01 |
| 调度逐租户容错 + UNIQUE 幂等 | ✅ P-U13-02 |
| poll FOR UPDATE SKIP LOCKED 防重复 | ✅ P-U13-02 |
| exchange 一次性 cred_token + 5min TTL | ✅ P-U13-02 |
| poll 响应无明文 / exchange 才返回 | ✅ P-U13-02 |
| result→upload_for_crawler + import_batch_id 回填 | ✅ P-U13-03 |
| 失败联动 report_failure / 成功 report_success | ✅ P-U13-03 |
| 3 adapter 反查 + 未匹配 record warning 不阻塞 | ✅ P-U13-03 |
| qianniu_daily/ad_daily UNIQUE upsert 幂等 | ✅ P-U13-03 |
| data quality summary source×severity | ✅ P-U13-03 |
