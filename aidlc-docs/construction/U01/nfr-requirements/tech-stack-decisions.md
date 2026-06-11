# U01 技术栈决策（Tech Stack Decisions）

> 基于 15 个 NFR 决策问题答案，列出 U01 单元的具体技术选型与版本锁定。

---

## 1. 选型一览表

| 类别 | 决策 | 选型 | 版本 | 决策依据 |
|---|---|---|---|---|
| JWT 库 | Q1=A | **PyJWT** | ≥2.8 | 社区主流、文档齐全、与 FastAPI 例子高度匹配 |
| 密码哈希 | Q2=A | **passlib[bcrypt]** | ≥1.7.4 | 算法升级路径清晰，未来切 argon2 / scrypt 仅改配置 |
| API 限流 | Q3=A | **slowapi** | ≥0.1.9 | 需求文档已锁定，FastAPI 友好 |
| Redis 客户端 | Q4=A | **redis** (with asyncio) | ≥5.0 | 官方主流，aioredis 已并入 |
| 数据库连接池 | Q5=C | SQLAlchemy `pool_size=5, max_overflow=10` | — | 保守配置，匹配 Zeabur PostgreSQL 默认配额 |
| 缓存 TTL | Q6=B | 权限 5min / 限流 15min / 黑名单 7d | — | 保守的权限缓存，避免权限变更滞后 |
| Token 清理 | Q7=B | Celery Beat 每天 04:00（备份后） | — | 频率适中，与备份链路串行 |
| 结构化日志 | Q8=A | **structlog** | ≥24.1 | JSON 输出 + 中间件友好 + 性能好 |
| 监控指标 | Q9=A | **prometheus-fastapi-instrumentator** | ≥7.0 | FastAPI 一行集成，Zeabur 兼容 |
| 异常追踪 | Q10=A | **Sentry SDK** | ≥2.0 | 免费层 5K 事件/月足够 MVP |
| 测试框架 | Q11=A | **pytest + pytest-asyncio + pytest-cov + httpx** | latest | Python 异步测试黄金组合 |
| 测试数据库 | Q12=B | 共享 PG + 事务回滚 | — | 启动快、与 RLS 兼容 |
| 多租户测试 | Q13=B | 典型实体集成测试 + 基类单测 | — | 平衡覆盖与速度 |
| RLS 开发 | Q14=B | 本地启用 RLS + BYPASS 角色调试 | — | 与生产一致，避免漏配 |
| 备份工具 | Q15=A | Celery subprocess + pg_dump | postgresql-client-16 | 简单、容器内自带 |

---

## 2. requirements.txt（U01 范围）

```text
# Web 框架
fastapi==0.115.0
uvicorn[standard]==0.30.6
pydantic==2.9.2
pydantic-settings==2.5.2

# 认证 & 加密
PyJWT==2.9.0
passlib[bcrypt]==1.7.4
cryptography>=43.0          # AES-256，U12 起重用

# 数据库
sqlalchemy[asyncio]==2.0.36
asyncpg==0.29.0             # PostgreSQL 异步 driver
alembic==1.13.3

# Redis & 缓存
redis==5.1.0                # 含 redis.asyncio

# 任务队列
celery==5.4.0
celery[redis]==5.4.0

# 限流
slowapi==0.1.9

# Cloudflare R2 (S3 兼容)
boto3==1.35.27              # 用于 R2 接入

# 日志 & 监控
structlog==24.4.0
python-json-logger==2.0.7
prometheus-fastapi-instrumentator==7.0.0
sentry-sdk[fastapi]==2.14.0

# HTTP 客户端
httpx==0.27.2

# 测试
pytest==8.3.3
pytest-asyncio==0.24.0
pytest-cov==5.0.0
pytest-postgresql==6.1.1
factory-boy==3.3.1          # 测试数据工厂

# 工具
python-dotenv==1.0.1
tenacity==9.0.0             # 重试装饰器
```

---

## 3. 关键配置代码片段

### 3.1 SQLAlchemy 引擎（core/db.py）

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.DB_ECHO,           # 仅本地 dev 开启
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)
```

### 3.2 PyJWT 编解码（core/security/auth.py）

```python
import jwt
from datetime import datetime, timedelta, timezone

ALGORITHM = "HS256"
ACCESS_EXPIRE = timedelta(minutes=30)
REFRESH_EXPIRE = timedelta(days=7)

def encode_access(claims: dict) -> str:
    payload = {
        **claims,
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + ACCESS_EXPIRE,
        "typ": "access",
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=ALGORITHM)

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[ALGORITHM])
```

### 3.3 passlib 密码哈希（core/security/auth.py）

```python
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)
```

### 3.4 slowapi 限流（main.py）

```python
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(
    key_func=get_remote_address,           # 同步取 client IP，不读 body
    storage_uri=settings.REDIS_URL,
    default_limits=["100/minute"],         # L1 全局限流
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# 在 router 上（仅 IP 级）：
@router.post("/login")
@limiter.limit("20/minute")                # L2 登录端点 IP 限流
async def login(request: Request, payload: LoginRequest):
    # 注意：username 维度的限流（L3）和账户锁定（L4）在 AuthService 内
    # 实施。不要在 slowapi key_func 中读 request.json()，因为它是异步调用
    # 且消费 body 会破坏后续 Pydantic 解析。
    return await auth_service.login(
        payload.username, payload.password, ip=get_remote_address(request)
    )
```

### 3.5 structlog 配置（core/logging.py）

```python
import structlog
import logging

logging.basicConfig(level=settings.LOG_LEVEL, format="%(message)s")

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # 敏感字段过滤
        _redact_sensitive,
        structlog.processors.JSONRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

def _redact_sensitive(_, __, event_dict):
    for key in ("password", "token", "secret", "access_token", "refresh_token"):
        if key in event_dict:
            event_dict[key] = "***REDACTED***"
    return event_dict
```

### 3.6 Prometheus 指标（main.py）

```python
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator(
    excluded_handlers=["/health", "/metrics"],
    should_group_status_codes=False,
).instrument(app).expose(app, endpoint="/metrics")

# 自定义业务指标
from prometheus_client import Counter

auth_login_total = Counter(
    "auth_login_total",
    "Login attempts",
    ["result"],  # success / failed / locked / rate_limited
)
```

### 3.7 Sentry 集成（main.py）

```python
import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

if settings.SENTRY_DSN:
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
        before_send=_strip_sensitive,
    )
```

### 3.8 Redis 异步客户端（core/cache.py）

```python
from redis.asyncio import Redis

redis_pool = Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    max_connections=20,
    socket_connect_timeout=5,
)
```

### 3.9 Celery + pg_dump 备份任务（tasks/backup_tasks.py）

```python
import subprocess
from pathlib import Path
import gzip
import hashlib

@celery_app.task(bind=True, max_retries=2)
def backup_database(self):
    out_path = Path("/tmp") / f"pg-{date.today():%Y-%m-%d}.sql.gz"
    cmd = [
        "pg_dump",
        "--format=plain",
        "--no-owner",
        "--no-acl",
        "--dbname", settings.DATABASE_URL_SYNC,
    ]
    with gzip.open(out_path, "wb") as fout:
        result = subprocess.run(cmd, stdout=fout, stderr=subprocess.PIPE, check=True)
    # ... 上传到 R2，写 backup_record
```

---

## 4. Dockerfile（backend，含 pg_dump 二进制）

```dockerfile
FROM python:3.12-slim AS base

# 安装 PostgreSQL 客户端（pg_dump）
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

> celery-worker 镜像复用同 Dockerfile，CMD 改为 `celery -A app.core.celery_app worker --loglevel=info`

---

## 5. RLS 策略示例（Alembic migration）

```sql
-- 启用 RLS
ALTER TABLE "user" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "user" FORCE ROW LEVEL SECURITY;

-- 应用主角色策略
CREATE POLICY tenant_isolation ON "user"
    FOR ALL
    TO clothing_app  -- 应用主角色
    USING (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    );

-- 创建 BYPASS 角色（开发本地调试 + 系统任务）
CREATE ROLE clothing_bypass BYPASSRLS;
GRANT clothing_app TO clothing_bypass;
```

> 本地开发：`DATABASE_URL` 用 `clothing_app` 角色（启用 RLS 验证）；调试可临时切到 `clothing_bypass`。

---

## 6. 测试 fixture 关键设计

```python
# tests/conftest.py

@pytest.fixture(scope="session")
def pg_db():
    # 启动一个共享 PostgreSQL（pytest-postgresql 自动管理）
    ...

@pytest.fixture(scope="function")
async def session(pg_db):
    """每个测试一个事务，结束自动回滚"""
    async with engine.connect() as conn:
        async with conn.begin() as trans:
            async_session = AsyncSession(bind=conn, expire_on_commit=False)
            yield async_session
            await trans.rollback()

@pytest.fixture
async def tenant_a(session):
    return await create_tenant(session, code="tenant_a")

@pytest.fixture
async def tenant_b(session):
    return await create_tenant(session, code="tenant_b")

# 多租户隔离测试样板
async def test_tenant_isolation_for_user(session, tenant_a, tenant_b):
    set_tenant_context(session, tenant_a.id)
    user_a = await create_user(session, username="alice")
    set_tenant_context(session, tenant_b.id)
    users = await list_users(session)
    assert user_a not in users  # 租户 B 看不到租户 A 的用户
```

---

## 7. 环境变量清单（.env.example）

```bash
# Database
DATABASE_URL=postgresql+asyncpg://clothing_app:password@localhost:5432/clothing_erp
DATABASE_URL_SYNC=postgresql://clothing_app:password@localhost:5432/clothing_erp
DB_ECHO=false

# Redis
REDIS_URL=redis://localhost:6379/0

# JWT
JWT_SECRET=<256-bit hex string>
JWT_ALGORITHM=HS256

# AES master key for credential encryption (U12)
CREDENTIAL_MASTER_KEY=<base64-encoded 32 bytes>

# CORS
CORS_ALLOWED_ORIGINS=https://app.clothinkai.com,http://localhost:5173

# Sentry
SENTRY_DSN=

# Logging
LOG_LEVEL=INFO

# Celery
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2

# R2
R2_ENDPOINT_URL=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET_PUBLIC=clothing-erp-public
R2_BUCKET_PRIVATE=clothing-erp-private
R2_BUCKET_BACKUPS=clothing-erp-backups

# Initial admin (仅首次启动用)
INITIAL_ADMIN_USERNAME=admin
```

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 15 个决策全部映射到具体技术选型 | ✅ |
| 与需求文档第 1.2 节技术栈一致 | ✅ |
| 与应用设计的 components.md 第 1 节项目结构一致 | ✅ |
| 与 functional-design business-rules 引用的工具一致（bcrypt cost=12 等） | ✅ |
| 关键配置都有代码片段示例 | ✅ |
| Dockerfile 含 pg_dump 客户端 | ✅ |
| 环境变量清单完整 | ✅ |
