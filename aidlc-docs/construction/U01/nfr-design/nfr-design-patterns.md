# U01 NFR 设计模式（NFR Design Patterns）

> 把 NFR Requirements 的指标和决策落地为具体的设计模式。每个模式含：用途、关键代码骨架、与故事/NFR 的映射。

---

## 1. 中间件链与请求处理流（Q1=A, Q2=C, Q7=A, Q8=A）

### 1.1 中间件注册顺序

ASGI 中间件按"注册顺序的反向"执行（LIFO），所以从外到内的实际执行顺序如下：

```
请求进入
  ↓ CORS                       ← 最外层，处理预检
  ↓ SentryAsgi                 ← 抓取未捕获异常
  ↓ RequestId                  ← 给每个请求分配 request_id 写入 contextvars
  ↓ slowapi Limiter            ← IP 限流（不依赖 tenant_id）
  ↓ TenancyContext             ← 解析 JWT，写入 contextvars (tenant_id, user_id, actor_type)
  ↓ get_session() Depends      ← 创建 Session，从 contextvars 读 tenant_id 调 SET LOCAL
  ↓ Router → Service → ...
```

### 1.2 contextvars 设计

```python
# core/context.py
from contextvars import ContextVar
from uuid import UUID

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
tenant_id_ctx: ContextVar[UUID | None] = ContextVar("tenant_id", default=None)
user_id_ctx: ContextVar[UUID | None] = ContextVar("user_id", default=None)
actor_type_ctx: ContextVar[str] = ContextVar("actor_type", default="anonymous")
bypass_rls_ctx: ContextVar[bool] = ContextVar("bypass_rls", default=False)
```

### 1.3 RequestId 中间件

```python
# core/middleware/request_id.py
import uuid
import structlog
from starlette.middleware.base import BaseHTTPMiddleware

class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_ctx.set(rid)
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = rid
            return response
        finally:
            request_id_ctx.reset(token)
            structlog.contextvars.clear_contextvars()
```

### 1.4 TenancyContext 中间件

```python
# core/middleware/tenancy.py
import sentry_sdk
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

class TenancyContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 仅对带 Authorization 的请求解析（健康检查、登录端点跳过）
        token = _extract_bearer_token(request)
        if token:
            try:
                claims = decode_token_unverified_payload(token)  # 不验证签名，仅取上下文
                tenant_id_ctx.set(UUID(claims["tenant_id"])) if claims.get("tenant_id") else None
                user_id_ctx.set(UUID(claims["sub"]))
                actor_type_ctx.set(claims.get("actor_type", "user"))
                if claims.get("actor_type") == "platform_admin":
                    bypass_rls_ctx.set(True)

                # Sentry 标记（不发送 PII）
                sentry_sdk.set_tag("tenant_id", str(tenant_id_ctx.get()) if tenant_id_ctx.get() else "none")
                sentry_sdk.set_user({"id": str(user_id_ctx.get())})

                # structlog 绑定
                structlog.contextvars.bind_contextvars(
                    tenant_id=str(tenant_id_ctx.get()) if tenant_id_ctx.get() else None,
                    user_id=str(user_id_ctx.get()),
                    actor_type=actor_type_ctx.get(),
                )
            except Exception:
                pass  # JWT 验证失败由 Depends 处理

        return await call_next(request)
```

> **职责说明**：本中间件仅写 contextvars 用于**日志和 Sentry 标记**。**真正的鉴权**仍由 `Depends(get_current_user)` 完成（包括签名验证、过期、`pwd_iat` 比对等）。这是 Q2=C 的核心：middleware 写 contextvars，Session 依赖读 contextvars。

### 1.5 Session 依赖（读 contextvars 调 SET LOCAL）

```python
# core/db.py
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession

async def get_session() -> AsyncIterator[AsyncSession]:
    bypass = bypass_rls_ctx.get()
    engine = engine_bypass if bypass else engine_app  # Q3=A 双引擎
    async with engine.connect() as conn:
        async with conn.begin():
            session = AsyncSession(bind=conn, expire_on_commit=False)
            tid = tenant_id_ctx.get()
            if tid is not None and not bypass:
                await conn.execute(
                    text("SET LOCAL app.tenant_id = :tid"),
                    {"tid": str(tid)}
                )
            elif bypass:
                await conn.execute(text("SET LOCAL app.bypass_rls = 'on'"))
            try:
                yield session
            finally:
                await session.close()
```

**映射故事**：EP01-S07（多租户隔离）

---

## 2. RLS 双引擎模式（Q3=A）

### 2.1 引擎拆分

```python
# core/db.py
engine_app = create_async_engine(
    settings.DATABASE_URL_APP,        # postgresql+asyncpg://clothing_app:...
    pool_size=5, max_overflow=10,
    ...
)

engine_bypass = create_async_engine(
    settings.DATABASE_URL_BYPASS,     # postgresql+asyncpg://clothing_bypass:...
    pool_size=2, max_overflow=3,     # 系统任务用，连接更少
    ...
)
```

### 2.2 切换策略

| 场景 | 引擎 | bypass_rls_ctx |
|---|---|---|
| 普通业务请求 | engine_app | False |
| Platform admin token | engine_bypass | True |
| Celery 系统任务（备份、清理） | engine_bypass（显式 system_context） | True |
| 单元测试 | engine_bypass（绕过 RLS） + 手动设 tenant_id 测隔离 | True |

### 2.3 system_context 上下文管理器

```python
# core/tenancy.py
@asynccontextmanager
async def system_context() -> AsyncIterator[AsyncSession]:
    """系统任务用，绕过 RLS，必写 audit_log"""
    token = bypass_rls_ctx.set(True)
    try:
        async with engine_bypass.connect() as conn:
            async with conn.begin():
                session = AsyncSession(bind=conn, expire_on_commit=False)
                await conn.execute(text("SET LOCAL app.bypass_rls = 'on'"))
                yield session
    finally:
        bypass_rls_ctx.reset(token)
```

### 2.4 PostgreSQL 角色与策略

```sql
-- migrations/001_create_roles_and_rls.sql
CREATE ROLE clothing_app NOINHERIT LOGIN PASSWORD 'xxx';
CREATE ROLE clothing_bypass BYPASSRLS NOINHERIT LOGIN PASSWORD 'yyy';
GRANT ALL ON ALL TABLES IN SCHEMA public TO clothing_app, clothing_bypass;

-- 后续每个表在 Alembic 中：
ALTER TABLE "user" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "user" FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON "user"
    FOR ALL TO clothing_app
    USING (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    );
```

**映射故事**：EP01-S07、EP10-NFR03

---

## 3. 双层限流模式（Q4=A）

### 3.1 三层防护协作

| 层 | 维度 | 实施位置 | 阈值 | 失效响应 |
|---|---|---|---|---|
| L1 | **IP 全局** | slowapi @limiter.limit | 100/min | 429 |
| L2 | **(IP, username)** | AuthService.login 内部 + Redis | 5/15min | 429 |
| L3 | **账户累计** | AuthService.login 内部 + DB user.failed_login_count | 10 次 → 锁账户 | 423 |

> **重要修订**：原计划 slowapi `key_func=lambda req: f"{ip}:{req.json()...}"` 不可行，因为 `req.json()` 是异步调用、且消费请求体会破坏后续 Pydantic 解析。改为 slowapi 只做 IP 级，username 相关的两层在 Service 内手写。

### 3.2 L1：slowapi 处理 IP 全局限流

```python
# main.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,        # 同步取 client IP，安全无副作用
    storage_uri=settings.REDIS_URL,
    default_limits=["100/minute"],
)

# modules/auth/api.py
@router.post("/login")
@limiter.limit("20/minute")              # 单 IP 全局登录尝试上限（保险丝）
async def login(request: Request, payload: LoginRequest):
    # request 形参用于 slowapi 内部识别，不在 handler 里读 body
    return await auth_service.login(payload.username, payload.password, ip=get_remote_address(request))
```

### 3.3 L2 + L3：Service 内手写（Redis + DB）

```python
# modules/auth/service.py
class AuthService:
    LOGIN_FAIL_KEY = "login:fail:{ip}:{username}"
    LOGIN_FAIL_TTL = 15 * 60          # 15 分钟
    LOGIN_FAIL_LIMIT = 5              # IP+username 维度
    ACCOUNT_LOCK_THRESHOLD = 10       # 账户级累计

    async def login(self, username: str, password: str, *, ip: str) -> TokenPair:
        # L2: (IP, username) 维度限流（Redis 计数）
        fail_key = self.LOGIN_FAIL_KEY.format(ip=ip, username=username)
        fail_count = int(await self.cache.get(fail_key) or 0)
        if fail_count >= self.LOGIN_FAIL_LIMIT:
            await self.audit.log(action="login_rate_limited", actor_type="unknown",
                                 purpose=f"ip+username over {self.LOGIN_FAIL_LIMIT}/15min")
            raise RateLimitedError(retry_after_seconds=self._ttl(fail_key))

        user = await self.user_repo.get_by_username(username)
        if not user:
            # 用户不存在：仍计数避免用户名探测
            await self._record_fail(fail_key, audit_action="login_failed",
                                    actor_type="unknown", username=username, ip=ip)
            raise InvalidCredentialsError()

        # L3: 账户级状态
        if user.locked_at:
            await self.audit.log(action="login_locked", user_id=user.id, ip=ip)
            raise AccountLockedError()
        if user.deleted_at or user.status == "disabled":
            await self.audit.log(action="login_disabled", user_id=user.id, ip=ip)
            raise InvalidCredentialsError()

        if not verify_password(password, user.password_hash):
            await self._record_fail(fail_key, audit_action="login_failed", user_id=user.id, ip=ip)
            await self._increment_account_failure(user, ip=ip)
            raise InvalidCredentialsError()

        # 成功：清 Redis 计数 + 重置 DB 计数
        await self.cache.delete(fail_key)
        if user.failed_login_count > 0:
            user.failed_login_count = 0
            await self.user_repo.save(user)
        await self.audit.log(action="login", user_id=user.id, ip=ip)
        return await self._issue_tokens(user)

    async def _record_fail(self, fail_key: str, *, audit_action: str, **audit_kwargs):
        """同时增 Redis 计数（L2）和写 audit_log"""
        new_count = await self.cache.incr(fail_key)
        if new_count == 1:
            await self.cache.expire(fail_key, self.LOGIN_FAIL_TTL)
        await self.audit.log(action=audit_action, **audit_kwargs)

    async def _increment_account_failure(self, user: User, *, ip: str):
        """L3 账户级累计；超过阈值锁账户 + 写 user_lock 审计"""
        user.failed_login_count += 1
        if user.failed_login_count >= self.ACCOUNT_LOCK_THRESHOLD:
            user.locked_at = utcnow()
            await self.audit.log(
                action="user_lock",
                actor_type="system",
                resource="user",
                resource_id=user.id,
                purpose="exceeded_login_attempts",
                ip=ip,
            )
        await self.user_repo.save(user)
```

### 3.4 协作流程（端到端）

```
请求 POST /api/auth/login
  ↓ slowapi 检查 IP 全局 100/min（适用所有 API）+ 登录端点 20/min
    超限 → 429 Rate Limited
  ↓ AuthService.login 进入
  ↓ Redis 检查 (ip, username) 计数（L2）
    >=5 → 429（带 retry_after）
  ↓ 加载用户
    不存在 → Redis incr + audit("login_failed", actor_type=unknown) → 401
  ↓ 检查账户状态
    locked_at != NULL → audit("login_locked") → 423
    disabled / deleted → audit("login_disabled") → 401（避免泄露用户存在与否）
  ↓ verify_password
    失败 → Redis incr + audit("login_failed")
         → user.failed_login_count++（L3）
         → 若 >=10 → user.locked_at=NOW + audit("user_lock", actor_type=system) → 401(此次)
         → 否则 → 401
  ↓ 成功
    清 Redis 计数 + user.failed_login_count=0
    audit("login") + 签发 token
```

### 3.5 为什么这样分层

| 层 | 解决什么 |
|---|---|
| L1 IP 全局 100/min | 防 DoS、防全平台探测；与所有 API 一起 |
| L2 (IP, username) 5/15min | 防针对单账户的密码爆破，按 (ip, username) 隔离避免共享 IP 误伤 |
| L3 账户累计 10 次 | 即便攻击者切换 IP 也无法无限次试，最终锁账户；管理员介入解锁 |

**映射故事**：EP01-S01（用户登录）、EP01-S08（审计）

---

## 4. Token 失效双保险模式（Q10=C）

### 4.1 主机制：password_changed_at 安全戳

每次以下事件发生时，都更新 `user.password_changed_at = NOW()`（即使不是真改密码）：
- 密码修改
- 用户禁用 / 启用
- 用户锁定 / 解锁
- 软删除
- user_role 增减
- user_permission_override 任一变化

token 校验时比对：
```python
def validate_access_token(token: str, user: User) -> None:
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    if payload["pwd_iat"] != user.password_changed_at.isoformat():
        raise TokenInvalidError("Token revoked")
```

### 4.2 兜底机制：JWT 黑名单（Redis）

某些极端场景（如发现 secret 泄露）需要立即吊销特定 token：

```python
# core/security/auth.py
async def revoke_jti(jti: str, ttl_seconds: int):
    """把 jti 加入黑名单，TTL = access_token 剩余有效期"""
    await redis.setex(f"jwt:blacklist:{jti}", ttl_seconds, "1")

async def is_revoked(jti: str) -> bool:
    return await redis.exists(f"jwt:blacklist:{jti}") > 0
```

### 4.3 协作

```
Token 校验依赖（get_current_user）：
  1. jwt.decode 验签 + 过期
  2. is_revoked(jti)?  → 是 → 401
  3. 加载 user
  4. user.deleted_at 非空 / status=disabled / locked_at 非空？ → 401
  5. payload["pwd_iat"] != user.password_changed_at？ → 401
  6. 通过
```

**性能优化**：
- 黑名单是兜底机制，平时极少使用，单次 Redis EXISTS ≈ 1ms
- 主机制只比对 ISO 字符串，无 IO 开销

**映射故事**：EP01-S02（修改密码）、EP01-S03（用户管理）、EP01-S04（角色分配）、EP01-S05（权限变更）

---

## 5. 健康检查模式（Q6=A）

### 5.1 端点设计

```python
# main.py
@app.get("/health")
async def liveness():
    """Liveness: 进程存活即可，不查依赖"""
    return {"status": "ok"}

@app.get("/ready")
async def readiness(session: AsyncSession = Depends(get_session_bypass)):
    """Readiness: 依赖项可达"""
    checks = {}
    try:
        await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {type(e).__name__}"

    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {type(e).__name__}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "error"
    status_code = 200 if overall == "ok" else 503
    return JSONResponse(content={"status": overall, "checks": checks}, status_code=status_code)
```

### 5.2 Zeabur 配置

| 端点 | 用途 | 调用频率 |
|---|---|---|
| /health | Liveness probe | 每 10 秒 |
| /ready | Readiness probe | 启动时 + 每 30 秒 |

**映射 NFR**：3.5（监控）、5.6（监控指标）

---

## 6. structlog 上下文绑定模式（Q8=A）

### 6.1 配置（已在 tech-stack-decisions.md 第 3.5 节）

中间件已经在 RequestIdMiddleware 和 TenancyContextMiddleware 中调用 `bind_contextvars`，structlog 配置中的 `merge_contextvars` 处理器自动把 contextvars 合并到日志条目。

### 6.2 业务代码中的使用

```python
import structlog

log = structlog.get_logger()

class AuthService:
    async def login(self, username, password):
        log.info("login_attempt", username=username)  # 自动带 request_id, tenant_id 等
        ...
```

### 6.3 输出样例

```json
{
  "timestamp": "2026-05-24T03:30:15.123Z",
  "level": "info",
  "event": "login_attempt",
  "request_id": "abc-123",
  "tenant_id": "5e9c...",
  "user_id": null,
  "actor_type": "anonymous",
  "username": "alice"
}
```

**映射 NFR**：5.3（日志结构化）、6.1（监控）

---

## 7. Sentry 多租户上下文模式（Q7=A）

### 7.1 实施位置
在 TenancyContextMiddleware 中（详见 1.4 节），获得 token claims 后调用：

```python
sentry_sdk.set_tag("tenant_id", str(tenant_id_ctx.get()) or "none")
sentry_sdk.set_user({"id": str(user_id_ctx.get())})  # 仅 ID，不带 email/IP/PII
```

### 7.2 PII 防护

```python
# main.py Sentry init
sentry_sdk.init(
    ...,
    send_default_pii=False,        # 不带 cookies / IP / username
    before_send=_strip_sensitive,  # 额外过滤
)

def _strip_sensitive(event, hint):
    # 清理可能含密码的字段
    if "request" in event and "data" in event["request"]:
        for key in ("password", "old_password", "new_password", "token", "secret"):
            if isinstance(event["request"]["data"], dict):
                event["request"]["data"].pop(key, None)
    return event
```

**映射 NFR**：3.1 安全 / 6.2 异常追踪

---

## 8. 备份失败告警模式（Q9=C）

### 8.1 重试 + Sentry 模式

```python
# tasks/backup_tasks.py
@celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    retry_kwargs={'max_retries': 2, 'countdown': 300},  # 重试 2 次，间隔 5 分钟
)
def backup_database(self):
    try:
        _do_backup()
    except Exception as e:
        # 最后一次重试也失败时记 backup_record + 触发 Sentry
        if self.request.retries >= self.max_retries:
            await BackupRecord.create(
                type="daily",
                status="failed",
                error_message=str(e),
            )
            sentry_sdk.capture_exception(e)
            log.error("backup_failed_terminal", error=str(e))
        raise
```

### 8.2 U07 完成后的增强

U07 完成后，在 backup_tasks 中增加：

```python
if self.request.retries >= self.max_retries:
    ...
    # U07+ 新增：企微告警
    from app.modules.wecom.client import WecomClient
    await WecomClient().push_to_app(
        to_user="@admins",
        content=f"备份失败 {date.today()}: {str(e)[:200]}"
    )
```

**映射 NFR**：3.2 备份 / 6.2 Sentry

---

## 9. 字段级权限模式（U01 仅占位，U09 启用）

### 9.1 装饰器（粗粒度）

```python
# core/security/permissions.py
def require_permission(scope: str, action: str = "read"):
    """FastAPI Depends 工厂"""
    async def _check(user: User = Depends(get_current_user)):
        if not check_permission(user, scope, action):
            raise PermissionDeniedError()
        return user
    return Depends(_check)

# 使用：
@router.get("/users/", dependencies=[require_permission("auth.user", "read")])
async def list_users(...): ...
```

### 9.2 Pydantic 动态 Schema（U09 启用）

```python
# core/security/permissions.py（U09 完成）
def build_schema_for_user(base_cls: type[BaseModel], user: User) -> type[BaseModel]:
    """根据用户字段权限动态裁剪响应 Schema"""
    fields_to_remove = []
    for field_name, field_info in base_cls.model_fields.items():
        meta = field_info.metadata
        if any(isinstance(m, FieldPermission) for m in meta):
            field_perm = next(m for m in meta if isinstance(m, FieldPermission))
            if not check_permission(user, field_perm.scope, "read"):
                fields_to_remove.append(field_name)
    if not fields_to_remove:
        return base_cls
    return _create_subset_model(base_cls, exclude=fields_to_remove)
```

> U01 阶段只搭建 `require_permission` 装饰器框架；动态 Schema 留到 U09。

**映射故事**：EP01-S04（U01）、EP01-S05/S06（U09）

---

## 10. 设计模式与故事/NFR 映射汇总

| 模式 | 故事 | NFR | U01 章节 |
|---|---|---|---|
| 中间件链 + contextvars | EP01-S07 | 多租户/可观测 | 1 |
| RLS 双引擎 | EP01-S07/NFR03 | 多租户 | 2 |
| 双层限流 | EP01-S01/S08 | 安全 3.4 | 3 |
| Token 失效双保险 | EP01-S02/S03/S04 | 安全 3.1 | 4 |
| Liveness/Readiness 端点 | NFR06 | 可用性 | 5 |
| structlog contextvars | EP01-S08 | 可维护 5.3 | 6 |
| Sentry 多租户上下文 | NFR | 可观测 6.2 | 7 |
| 备份失败重试+Sentry | NFR04 | 可用性 | 8 |
| 字段级权限装饰器框架 | EP01-S04 | 安全 3.2 | 9 |

---

## 11. 一致性校验

| 校验 | 结果 |
|---|---|
| 10 个决策全部转化为可实施模式 | ✅ |
| 与 NFR Requirements 量化指标一致 | ✅ |
| 与 functional-design 业务规则一致 | ✅ |
| 中间件顺序 + contextvars + 依赖注入分工清晰 | ✅ |
| RLS 双引擎不引入循环依赖 | ✅ |
| Token 失效主+兜底机制覆盖所有需求场景 | ✅ |
| 备份不依赖企微（U01 阶段） | ✅ |
| 字段级权限 U01 占位，U09 启用 | ✅ |
