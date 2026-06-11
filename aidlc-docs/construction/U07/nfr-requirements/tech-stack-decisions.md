# U07 技术栈决策（Tech Stack Decisions）

> 单元：U07 — 企微集成基础
> 原则：复用 U01-U06 技术栈，**零新增运行时依赖**（httpx / cryptography 已 pin）

---

## 1. 依赖确认（无新增）

| 用途 | 库 | 版本 | 状态 |
|---|---|---|---|
| 企微 HTTP 调用 | httpx | 0.27.2 | ✅ 已 pin（U01） |
| AES-256-GCM + HKDF | cryptography | 43.0.1 | ✅ 已 pin（U01） |
| Redis（token 缓存） | redis | 5.1.0 | ✅ 已 pin |
| Celery（扫描/执行） | celery | 5.4.0 | ✅ 已 pin |
| 测试 mock | pytest-mock（monkeypatch） | — | ✅ 已有 |

> requirements.txt / requirements-dev.txt **不改动**。

---

## 2. 凭据加密落地（crypto.py 实现 U12 占位）

```python
# core/security/crypto.py（U07 落地 encrypt/decrypt；rotate 仍 U12 占位）
import base64
from uuid import UUID
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from app.core.config import settings
import os

def _derive_key(tenant_id: UUID) -> bytes:
    master = base64.b64decode(settings.CREDENTIAL_MASTER_KEY.get_secret_value())
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32,
                salt=tenant_id.bytes, info=b"wecom-credential")
    return hkdf.derive(master)

def encrypt_credential(tenant_id: UUID, plaintext: str) -> bytes:
    key = _derive_key(tenant_id)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return nonce + ct   # nonce(12) || ciphertext || tag(16)

def decrypt_credential(tenant_id: UUID, _credential_id, ciphertext: bytes, *, purpose: str) -> str:
    key = _derive_key(tenant_id)
    nonce, ct = ciphertext[:12], ciphertext[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()   # tag 校验失败抛 InvalidTag
```

- 跨租户：salt=tenant_id → A 密钥不可解 B 密文（NFR §4.1）。
- 篡改：`AESGCM.decrypt` tag 校验失败抛 `InvalidTag` → 上层转 500 WECOM_DECRYPT_FAILED，不静默。
- 解密由 service 包一层 `@audit("wecom.secret.decrypt")`。

---

## 3. access_token 缓存

```python
# modules/wecom/client.py（节选）
TOKEN_KEY = "wecom:token:{tenant_id}"

async def get_access_token(self) -> str:
    key = TOKEN_KEY.format(tenant_id=self._tenant_id)
    cached = await cache.get(key)
    if cached:
        return cached
    secret = decrypt_credential(self._tenant_id, None, self._cfg.secret_ciphertext, purpose="wecom_send")
    resp = self._http.get(f"{settings.WECOM_API_BASE}/cgi-bin/gettoken",
                          params={"corpid": self._cfg.corp_id, "corpsecret": secret})
    data = resp.json()
    if data.get("errcode"):
        raise WecomApiError(data["errcode"], data.get("errmsg"))
    await cache.set(key, data["access_token"], ex=settings.WECOM_TOKEN_TTL)  # 7000
    return data["access_token"]
```

- token 失效错误码（40014/42001）：`cache.delete(key)` + 刷新 + 重试一次。

---

## 4. WecomClient 骨架（httpx）

```python
class WecomClient:
    def __init__(self, tenant_id, cfg, http: httpx.Client | None = None):
        self._tenant_id, self._cfg = tenant_id, cfg
        self._http = http or httpx.Client(timeout=settings.WECOM_HTTP_TIMEOUT)  # 10s

    def get_access_token(self) -> str: ...
    def find_external_userid_by_wechat(self, wechat: str) -> str | None: ...  # 获取客户列表匹配
    def send_external_msg_template(self, *, sender: str, recipients: list[str],
                                   content: str) -> dict: ...                   # add_msg_template
    def verify_callback_signature(self, msg_signature, timestamp, nonce, encrypt) -> bool: ...
    def decrypt_callback(self, encrypt: str) -> dict: ...                       # EncodingAESKey AES-CBC
```

- errcode≠0 抛 `WecomApiError`；频控类错误码映射 `WecomRateLimited`；token 失效映射 `WecomTokenExpired`。
- 测试：monkeypatch 替换 WecomClient 方法返回桩数据。

---

## 5. 新增配置项（core/config.py）

```python
WECOM_API_BASE: str = "https://qyapi.weixin.qq.com"
WECOM_HTTP_TIMEOUT: int = 10           # 秒
WECOM_TOKEN_TTL: int = 7000            # access_token 缓存 TTL（企微 7200 留余量）
WECOM_URGE_SCAN_CRON: str = "0 9 * * *"  # Celery Beat 催发扫描（Asia/Shanghai）
# CREDENTIAL_MASTER_KEY 已存在（复用）
```

- 均有默认值，`.env.example` 追加说明；不强制配置即可启动（未配 wecom_config 时扫描跳过该租户）。

---

## 6. Celery Beat 注册（celery_app.py 扩展）

```python
celery_app.conf.beat_schedule["wecom-urge-scan"] = {
    "task": "app.tasks.wecom_tasks.scan_and_dispatch_urge",
    "schedule": crontab(hour=9, minute=0),   # 由 WECOM_URGE_SCAN_CRON 解析
}
```

- 复用 U01 Celery Beat 基线 + default 队列；`execute_wecom_message` 由扫描 `.delay()` 投递。

---

## 7. Prometheus 指标（core/metrics.py 扩展，4 个）

```python
wecom_message_total = Counter("wecom_message_total", "...", ["status"])
wecom_send_duration_seconds = Histogram("wecom_send_duration_seconds", "...")
wecom_rate_limited_total = Counter("wecom_rate_limited_total", "...", ["reason"])
wecom_callback_total = Counter("wecom_callback_total", "...", ["result"])
```

---

## 8. 环境变量清单（追加 .env.example）

| 变量 | 默认 | 说明 |
|---|---|---|
| WECOM_API_BASE | https://qyapi.weixin.qq.com | 企微 API 域名 |
| WECOM_HTTP_TIMEOUT | 10 | 外部调用超时（秒） |
| WECOM_TOKEN_TTL | 7000 | access_token 缓存 TTL |
| WECOM_URGE_SCAN_CRON | 0 9 * * * | 催发扫描调度 |
| CREDENTIAL_MASTER_KEY | （已有） | AES master key（base64 32B） |

---

## 9. 一致性校验

| 校验 | 结果 |
|---|---|
| 零新增运行时依赖 | ✅ httpx/cryptography 已 pin |
| AES-256-GCM + 每租户 HKDF 落地 crypto.py | ✅ §2 |
| access_token Redis 缓存 + 失效重试 | ✅ §3 |
| WecomClient httpx 超时 + 异常映射 | ✅ §4 |
| 4 配置项有默认值 + 复用 CREDENTIAL_MASTER_KEY | ✅ §5 |
| Celery Beat 扫描 + execute delay | ✅ §6 |
| 4 Prometheus 指标 | ✅ §7 |
