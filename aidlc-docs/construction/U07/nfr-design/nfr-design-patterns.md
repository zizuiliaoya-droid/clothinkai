# U07 NFR 设计模式（NFR Design Patterns）

> 单元：U07 — 企微集成基础
> 范围：5 个增量模式 P-U07-01~05；其余继承 U01-U06
> 关键：凭据加密落地 / 异步外部调用 + token 缓存 / 扫描编排逐租户 / 每消息事务频控降级 / 回调签名幂等

---

## 0. 继承声明

| 模式 | 来源 | 依赖点 |
|---|---|---|
| RLS 双引擎 + ORM 钩子 | U01 | 多租户隔离 |
| system_context + per-row/per-task SET LOCAL（set_config is_local=true） | U06a NF-1 | Celery 逐租户 |
| Celery Beat + asyncio.run 任务入口 + worker_process_init | U01 + U06a | 扫描/执行任务 |
| audit 装饰器 + structlog redact | U01 | secret 解密 / 回调审计 |
| urge_calculator（URGE_STATUS_SQL_EXPR + get_today） | U04 | 催发候选筛选 |

---

## P-U07-01：凭据加密落地（AES-256-GCM + 每租户 HKDF）

### 问题
`core/security/crypto.py` 当前 U12 占位（抛 NotImplementedError）；U07 需真实加密 wecom secret，且不能让 A 租户解 B 密文。

### 方案
```python
# core/security/crypto.py（落地 encrypt/decrypt；rotate 仍占位）
import base64, os
from uuid import UUID
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from app.core.config import settings

def _derive_key(tenant_id: UUID) -> bytes:
    master = base64.b64decode(settings.CREDENTIAL_MASTER_KEY.get_secret_value())
    return HKDF(algorithm=hashes.SHA256(), length=32,
                salt=tenant_id.bytes, info=b"wecom-credential").derive(master)

def encrypt_credential(tenant_id: UUID, plaintext: str) -> bytes:
    nonce = os.urandom(12)
    ct = AESGCM(_derive_key(tenant_id)).encrypt(nonce, plaintext.encode(), None)
    return nonce + ct  # nonce(12) || ciphertext || tag(16)

def decrypt_credential(tenant_id, credential_id, ciphertext: bytes, *, purpose: str) -> str:
    nonce, ct = ciphertext[:12], ciphertext[12:]
    try:
        return AESGCM(_derive_key(tenant_id)).decrypt(nonce, ct, None).decode()
    except Exception as exc:  # InvalidTag
        raise CredentialDecryptError("wecom secret 解密失败") from exc
```

### service 包装（审计）
```python
# modules/wecom/service.py
class WecomConfigService:
    async def _decrypt_secret(self, cfg) -> str:
        # @audit 在调用处（system_context, actor_type=system）记 wecom.secret.decrypt
        return decrypt_credential(cfg.tenant_id, cfg.id, cfg.secret_ciphertext, purpose="wecom_send")
```

### 关键点
- salt=tenant_id.bytes → 每租户独立密钥（跨租户不可解）。
- GCM tag 校验失败 → CredentialDecryptError → 500 WECOM_DECRYPT_FAILED（不静默）。
- 密钥每次派生不缓存于进程（降泄露面，开销 <1ms）。
- Schema 响应永不含明文（仅 secret_configured: bool）。

---

## P-U07-02：WecomClient 异步封装 + access_token Redis 缓存

### 方案
```python
# modules/wecom/client.py
class WecomClient:
    def __init__(self, tenant_id, cfg, *, http: httpx.AsyncClient, secret_provider):
        self._tid, self._cfg, self._http = tenant_id, cfg, http
        self._secret_provider = secret_provider  # async () -> str（解密 + 审计）

    async def get_access_token(self, *, force_refresh=False) -> str:
        key = f"wecom:token:{self._tid}"
        if not force_refresh:
            if (tok := await cache.get(key)):
                return tok
        secret = await self._secret_provider()
        data = (await self._http.get(f"{settings.WECOM_API_BASE}/cgi-bin/gettoken",
                params={"corpid": self._cfg.corp_id, "corpsecret": secret})).json()
        if data.get("errcode"):
            raise WecomApiError(data["errcode"], data.get("errmsg"))
        await cache.set_with_ttl(key, data["access_token"], settings.WECOM_TOKEN_TTL)  # 7000
        return data["access_token"]

    async def _call(self, method, path, **kw) -> dict:
        token = await self.get_access_token()
        resp = await self._http.request(method, f"{settings.WECOM_API_BASE}{path}",
                                        params={"access_token": token}, **kw)
        data = resp.json()
        if data.get("errcode") in (40014, 42001):       # token 失效 → 刷新重试一次
            token = await self.get_access_token(force_refresh=True)
            data = (await self._http.request(method, f"{settings.WECOM_API_BASE}{path}",
                    params={"access_token": token}, **kw)).json()
        if data.get("errcode") in _RATE_LIMIT_ERRCODES:  # 频控类
            raise WecomRateLimited(data["errcode"], data.get("errmsg"))
        if data.get("errcode"):
            raise WecomApiError(data["errcode"], data.get("errmsg"))
        return data

    async def find_external_userid_by_wechat(self, wechat: str) -> str | None: ...  # 客户列表匹配
    async def send_external_msg_template(self, *, sender, recipients, content) -> dict:
        with wecom_send_duration_seconds.time():
            return await self._call("POST", "/cgi-bin/externalcontact/add_msg_template",
                json={"chat_type": "single", "external_userid": recipients,
                      "sender": sender, "text": {"content": content}})
```

### 关键点
- httpx.AsyncClient timeout=WECOM_HTTP_TIMEOUT（10s）。
- token Redis 缓存 7000s；40014/42001 删缓存刷新重试一次。
- 频控错误码 → WecomRateLimited（执行层转降级）；其余 errcode → WecomApiError。
- 测试 monkeypatch 替换方法。

---

## P-U07-03：扫描编排（Beat → 逐租户 → 聚合 → 建 message → execute.delay）

### 方案
```python
# app/tasks/wecom_tasks.py
@celery_app.task(name="app.tasks.wecom_tasks.scan_and_dispatch_urge", queue="default")
def scan_and_dispatch_urge() -> dict:
    return asyncio.run(_scan_and_dispatch())

async def _scan_and_dispatch() -> dict:
    today = get_today()
    async with AsyncSessionBypass() as meta:                       # 系统级读租户清单
        tenant_ids = (await meta.execute(
            text("SELECT tenant_id FROM wecom_config WHERE is_active = true"))).scalars().all()
    total = 0
    for tid in tenant_ids:
        with system_context(tenant_id=tid):
            async with AsyncSessionApp() as s:
                await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
                svc = WecomScanService(s)
                total += await svc.scan_tenant(today)               # 见下
                await s.commit()
    return {"dispatched": total}

# modules/wecom/scan_service.py
class WecomScanService:
    async def scan_tenant(self, today) -> int:
        promos = await PromotionRepository(self._s).find_urge_candidates(
            today=today, urge_days=10, important_days=3)            # urge_status ∈ {催发,重要催发,超时}
        groups = defaultdict(list)
        for p in promos: groups[(p.blogger_id, p.pr_id)].append(p)
        templates = await self._load_templates()                   # urge / urge_important
        count = 0
        for (blogger_id, pr_id), items in groups.items():
            if await self._repo.exists_today_non_failed(blogger_id, pr_id, today):
                continue                                            # 幂等（BR-U07-34）
            contact = await self._repo.get_contact(blogger_id)
            if contact is None:
                await self._notify.notify([pr_id], f"博主 {items[0].blogger_nickname} 未绑定企微，无法自动催发")
                continue                                            # BR-U07-33
            important = any(_is_important(p, today) for p in items)
            tt = "urge_important" if important else "urge"
            content = render_template(templates[tt], _ctx(items[0], today))
            msg = self._repo.create_message(blogger_id, pr_id, contact.external_userid,
                    tt, content, [p.id for p in items])             # status=pending
            await self._s.flush()
            execute_wecom_message.delay(str(msg.id))                # BR-U07-35
            count += 1
        return count
```

### 关键点
- 逐租户 set_config（NF-1）；system_context 供 audit/notify。
- 聚合 (blogger_id, pr_id)；template_type 取最紧急。
- 幂等：同组当天非 failed message 已存在 → 跳过。
- 未绑定博主 → notify PR，不建 pending。
- `find_urge_candidates` 为 U07 在 PromotionRepository 新增的查询（复用 URGE_STATUS_SQL_EXPR）。

---

## P-U07-04：群发执行 + 频控降级（每消息独立事务）

### 方案
```python
@celery_app.task(bind=True, name="app.tasks.wecom_tasks.execute_wecom_message",
                 queue="default", autoretry_for=(OperationalError,), max_retries=1)
def execute_wecom_message(self, message_id: str) -> dict:
    return asyncio.run(_execute_one(UUID(message_id)))

async def _execute_one(message_id) -> dict:
    async with AsyncSessionBypass() as meta:                       # 读 message 元数据 + tenant
        msg = await meta.get(WecomMessage, message_id)
        if msg is None or msg.status != "pending":
            return {"status": "skipped"}                           # 幂等
        tid = msg.tenant_id
    with system_context(tenant_id=tid):
        async with AsyncSessionApp() as s:
            await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tid)})
            svc = WecomSendService(s)
            result = await svc.send(message_id)                    # 见下
            await s.commit()
    return result

# modules/wecom/send_service.py
class WecomSendService:
    async def send(self, message_id) -> dict:
        msg = await self._repo.get_message_for_update(message_id)
        if msg.status != "pending": return {"status": msg.status}  # 幂等
        today = get_today()
        if await self._repo.count_today(blogger_id=msg.blogger_id, today=today) >= 1:
            return await self._degrade(msg, "blogger")             # BR-U07-41
        if await self._repo.count_today(pr_id=msg.pr_id, today=today) >= 1:
            return await self._degrade(msg, "pr")                  # BR-U07-42
        try:
            client = await self._build_client(msg.tenant_id)
            resp = await client.send_external_msg_template(
                sender=self._cfg.default_sender_userid,
                recipients=[msg.external_userid], content=msg.rendered_content)
            msg.wecom_msgid = resp.get("msgid"); msg.status = "created"
            wecom_message_total.labels(status="created").inc()
            return {"status": "created"}
        except WecomRateLimited:
            return await self._degrade(msg, "api")                 # BR-U07-46
        except WecomApiError as e:
            msg.status = "failed"; msg.error_detail = str(e)
            wecom_message_total.labels(status="failed").inc()
            return {"status": "failed"}

    async def _degrade(self, msg, reason) -> dict:
        msg.status = "rate_limited"; msg.error_detail = f"频控降级:{reason}"
        await self._notify.notify([msg.pr_id], f"请手动催发 {await self._blogger_name(msg)}")
        wecom_rate_limited_total.labels(reason=reason).inc()
        wecom_message_total.labels(status="rate_limited").inc()
        return {"status": "rate_limited"}
```

### 关键点
- 每 message 独立事务；频控判定（DB 当天计数 status∈{created,sent}）先于发送。
- 命中频控 → rate_limited + notify PR，不调企微。
- count_today 命中 `idx(tenant_id, blogger_id, created_at)` / `idx(tenant_id, pr_id, created_at)`。
- 基础设施异常 autoretry=1；业务结果落 status 不重试。

---

## P-U07-05：回调签名校验 + 幂等状态推进

### 方案
```python
# modules/wecom/callback_api.py（公开，无 JWT）
@router.get("/api/wecom/callback/{tenant_id}")
async def verify_url(tenant_id, msg_signature, timestamp, nonce, echostr):
    cfg = await load_config_bypass(tenant_id)
    if not WecomCrypto(cfg).verify(msg_signature, timestamp, nonce, echostr):
        await audit_invalid("wecom.callback.invalid_signature", tenant_id, request)
        wecom_callback_total.labels(result="invalid_signature").inc()
        raise HTTPException(403)
    return PlainTextResponse(WecomCrypto(cfg).decrypt(echostr))

@router.post("/api/wecom/callback/{tenant_id}")
async def receive(tenant_id, msg_signature, timestamp, nonce, body):
    cfg = await load_config_bypass(tenant_id)
    if not WecomCrypto(cfg).verify(msg_signature, timestamp, nonce, body.encrypt):
        await audit_invalid(...); wecom_callback_total.labels(result="invalid_signature").inc()
        raise HTTPException(403)                                   # BR-U07-51
    payload = WecomCrypto(cfg).decrypt_payload(body.encrypt)       # → (msgid, result)
    async with AsyncSessionApp() as s:                             # set_config tenant
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant_id)})
        msg = await WecomMessageRepository(s).find_by_msgid(payload.msgid)
        if msg is None or msg.status != "created":                 # 幂等（BR-U07-53/54）
            wecom_callback_total.labels(result="ignored").inc(); return "success"
        msg.status = {"success":"sent","reject":"rejected","fail":"failed"}[payload.result]
        if msg.status == "sent": msg.sent_at = func.now()
        await s.commit()
        wecom_callback_total.labels(result=msg.status).inc()
    return "success"
```

### 关键点
- tenant 路由 `/callback/{tenant_id}`（避免 corp_id 全表扫描）。
- 签名 sha1(sorted(token,timestamp,nonce,encrypt)) 校验，失败 403 + audit。
- 幂等：未知 msgid / 非 created 状态 → 200 忽略。
- 仅 created → sent/rejected/failed。

---

## 一致性校验

| 校验 | 结果 |
|---|---|
| 凭据加密每租户密钥 + tag 防篡改（不静默） | ✅ P-U07-01 |
| WecomClient 异步 + token 缓存 7000s + 失效重试一次 | ✅ P-U07-02 |
| 扫描逐租户 set_config（NF-1）+ 聚合 + 幂等 + 未绑定跳过 | ✅ P-U07-03 |
| 每消息独立事务 + 频控 DB 计数 + 降级 notify | ✅ P-U07-04 |
| 回调签名校验 403+audit + 幂等状态推进 | ✅ P-U07-05 |
| adapter 不自 commit 不适用（U07 service 自管事务，HTTP/Task 边界） | ✅ |
| 4 Prometheus 指标接入 | ✅ P-U07-02/04/05 |
