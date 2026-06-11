# U01 逻辑组件（Logical Components）

> 列出 U01 单元的逻辑组件清单（含横切组件 + auth 模块组件）+ 各组件的职责、依赖、配置要点。

---

## 1. 组件分类

| 类别 | 组件数量 | 用途 |
|---|---|---|
| 横切核心（core/） | 12 | 配置、DB、缓存、安全、日志、中间件、状态机、附件 |
| 中间件（core/middleware/） | 4 | CORS / SentryAsgi / RequestId / TenancyContext |
| 业务模块（modules/auth/） | 5 | api / service / domain / repository / models / schemas |
| 任务（tasks/） | 2 | backup_tasks / cleanup_tasks |
| 数据库（alembic/） | 3 | seed / RLS / 索引 |
| **U01 合计** | **26 个一级组件** | — |

---

## 2. 横切核心组件（core/）

### 2.1 core/config.py
**职责**：环境变量加载与校验，使用 Pydantic Settings  
**关键配置**：
```python
class Settings(BaseSettings):
    # Database
    DATABASE_URL_APP: str            # asyncpg, role=clothing_app
    DATABASE_URL_BYPASS: str         # asyncpg, role=clothing_bypass
    DATABASE_URL_SYNC: str           # psycopg2, for pg_dump (Celery)

    # Redis
    REDIS_URL: str

    # JWT
    JWT_SECRET: SecretStr
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS
    CORS_ALLOWED_ORIGINS: list[str]

    # Sentry
    SENTRY_DSN: str | None = None

    # Initial admin
    INITIAL_ADMIN_USERNAME: str = "admin"

    # Backup
    BACKUP_RETAIN_DAILY_DAYS: int = 30
    BACKUP_RETAIN_MONTHLY_MONTHS: int = 12

    # R2
    R2_ENDPOINT_URL: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: SecretStr | None = None
    R2_BUCKET_BACKUPS: str = "clothing-erp-backups"
```

### 2.2 core/db.py
**职责**：双 SQLAlchemy 引擎 + Session 工厂 + tenant_id 注入  
**关键内容**：
- `engine_app`（pool_size=5, overflow=10）
- `engine_bypass`（pool_size=2, overflow=3）
- `get_session()` Depends：根据 `bypass_rls_ctx` 选择引擎，调 SET LOCAL
- `TenantScopedModel` 基类 + `before_compile` 事件钩子

### 2.3 core/cache.py
**职责**：Redis 异步客户端封装  
**接口**：
```python
class CacheClient:
    async def set_with_ttl(self, key: str, value: str, ttl: int) -> None
    async def incr(self, key: str, ttl: int | None = None) -> int
    async def exists(self, key: str) -> bool
    async def delete(self, key: str) -> None
```
**TTL 规约**（Q6=B）：
- 权限缓存：300 秒（5 min）
- 限流计数：900 秒（15 min）
- JWT 黑名单：≤ 1800 秒（access_token 剩余有效期）

### 2.4 core/celery_app.py
**职责**：Celery 应用 + Beat 调度  
**默认调度**：
- 03:00 daily：backup_database
- 04:00 daily：cleanup_expired_refresh_tokens + cleanup_expired_backups
- 04:00 月初：archive_audit_logs

**队列拆分**（Q5/U07/Q9 系列决策综合）：
- `default`：通用
- `backup`：备份相关
- `wecom`（U07 启用）

### 2.5 core/security/auth.py
**职责**：JWT 编解码 + 密码哈希  
**接口**：
- `encode_access(claims)` / `encode_refresh(claims)` / `decode_token(token)`
- `hash_password(plain)` / `verify_password(plain, hashed)`
- `revoke_jti(jti, ttl)` / `is_revoked(jti)`

### 2.6 core/security/permissions.py
**职责**：RBAC 校验 + 装饰器 + （U09 启用）字段级 Schema  
**接口**：
- `check_permission(user, scope, action) -> bool`
- `require_permission(scope, action="read") -> Depends`
- `get_effective_permissions(user) -> dict[str, set[str]]`（带 Redis 缓存）
- `build_schema_for_user(base_cls, user) -> type[BaseModel]`（U09）

### 2.7 core/security/rls.py
**职责**：RLS 策略管理（migration 用，运行时无操作）  
**内容**：策略 SQL 模板 + 表清单生成器，给 Alembic migration 使用

### 2.8 core/tenancy.py
**职责**：tenant_id 上下文管理 + system_context 上下文管理器  
**接口**：
- `system_context() -> AsyncIterator[AsyncSession]`（异步 with）

### 2.9 core/audit.py
**职责**：审计日志写入 + 装饰器 + ORM 钩子  
**接口**：
- `audit(operation: str, resource: str | None = None) -> Callable`（API 装饰器）
- `register_audit_listeners(model: type[Base], events: list[str])`（ORM 钩子注册）
- `AuditService.log(...)`（显式调用）
- `AuditService.query(...)`（查询）

### 2.10 core/exceptions.py
**职责**：异常体系  
**关键异常类**：
```python
class AppException(Exception):
    code: str
    status_code: int
    message: str

class InvalidCredentialsError(AppException): ...      # 401
class AccountLockedError(AppException): ...           # 423
class PasswordMustChangeError(AppException): ...      # 423
class TokenInvalidError(AppException): ...            # 401
class PermissionDeniedError(AppException): ...        # 403
class TenantContextMissingError(AppException): ...    # 500
class TenantContextMismatchError(AppException): ...   # 500
class RateLimitedError(AppException): ...             # 429
class ResourceNotFoundError(AppException): ...        # 404
class DuplicateResourceError(AppException): ...       # 409
```

### 2.11 core/errors.py
**职责**：FastAPI 全局异常处理器，把 AppException 转 JSON 响应  
**输出格式**（与需求第 16 节一致）：
```json
{ "code": "INVALID_CREDENTIALS", "message": "...", "details": {} }
```

### 2.12 core/logging.py
**职责**：structlog 配置 + 敏感字段 redact

---

## 3. 中间件（core/middleware/）

### 3.1 CORSMiddleware
**职责**：FastAPI 内置，配置 origins / methods / headers  
**配置**：origins 来自 `settings.CORS_ALLOWED_ORIGINS`

### 3.2 SentryAsgiMiddleware
**职责**：sentry-sdk 自带，捕获未处理异常

### 3.3 RequestIdMiddleware
**职责**：分配 request_id 写入 contextvars + structlog（详见 nfr-design-patterns.md 1.3）

### 3.4 TenancyContextMiddleware
**职责**：解析 JWT 写 contextvars + Sentry tag + structlog bind（详见 patterns.md 1.4）

---

## 4. 业务模块组件（modules/auth/）

### 4.1 modules/auth/api.py
**职责**：FastAPI Router  
**端点清单**：
| 方法 | 路径 | 故事 | 限流 |
|---|---|---|---|
| POST | `/api/auth/login` | EP01-S01 | L1: 全局 100/min + L2: 端点 20/min/IP（slowapi） + L3: (IP, username) 5/15min（Service+Redis） + L4: 账户累计 10 次锁（Service+DB） |
| POST | `/api/auth/refresh` | EP01-S01 | 20/min |
| POST | `/api/auth/logout` | — | 10/min |
| GET | `/api/auth/me` | — | 60/min |
| PUT | `/api/auth/password` | EP01-S02 | 10/min |
| POST | `/api/users/` | EP01-S03 | — |
| GET | `/api/users/` | EP01-S03 | — |
| PUT | `/api/users/{id}` | EP01-S03 | — |
| PUT | `/api/users/{id}/toggle` | EP01-S03 | — |
| PUT | `/api/users/{id}/unlock` | — | — |
| PUT | `/api/users/{id}/reset-password` | EP01-S03 | — |
| POST | `/api/users/{id}/roles` | EP01-S04 | — |
| GET | `/api/audit-logs` | EP01-S08 | — |

### 4.2 modules/auth/service.py
**职责**：编排 Domain + Repository  
**类**：`AuthService`、`UserService`、`PermissionService`（U09 启用字段级）

### 4.3 modules/auth/domain.py
**职责**：纯业务对象  
**关键类**：
- `Permissions`：权限计算的领域对象（封装 BR-PERM-001 算法）
- `LoginAttemptCounter`：登录失败计数器（Redis 操作封装）

### 4.4 modules/auth/repository.py
**职责**：SQLAlchemy 查询封装  
**类**：`UserRepository`、`RoleRepository`、`PermissionRepository`、`UserPermissionOverrideRepository`、`RefreshTokenRepository`、`AuditLogRepository`

### 4.5 modules/auth/models.py
**职责**：SQLAlchemy ORM 模型（详见 functional-design domain-entities.md）

### 4.6 modules/auth/schemas.py
**职责**：Pydantic 请求/响应 Schema  
**关键 Schema**：`LoginRequest`、`TokenPair`、`UserCreate`、`UserUpdate`、`UserOut`、`RoleAssignRequest`、`AuditLogQuery`、`AuditLogEntry`

### 4.7 modules/auth/permissions.py
**职责**：本模块的权限定义 + scope 常量  
**内容**：
```python
SCOPE_USER_READ = "auth.user:read"
SCOPE_USER_WRITE = "auth.user:write"
SCOPE_AUDIT_READ = "auth.audit:read"
# ...
```

### 4.8 modules/auth/default_roles.py
**职责**：10 个预设角色 + 默认权限矩阵（Q14=A 决策）  
**结构**：
```python
DEFAULT_ROLES: list[RoleSpec] = [
    RoleSpec(
        code="admin",
        name="管理员",
        permissions=["*"],  # 全部
    ),
    RoleSpec(
        code="pr",
        name="PR",
        permissions=["promotion.*:*", "blogger.*:read", "blogger.*:write", "report.publish_progress:read"],
    ),
    # ... 8 more
]
```
Alembic seed migration 读取此清单写 DB。

---

## 5. 任务组件（tasks/）

### 5.1 tasks/backup_tasks.py
**职责**：每日备份  
**任务**：
- `backup_database`：03:00 触发
- `cleanup_expired_backups`：04:00 触发

### 5.2 tasks/cleanup_tasks.py
**职责**：过期数据清理  
**任务**：
- `cleanup_expired_refresh_tokens`：04:30 触发，删除 expires_at < NOW() 的 refresh_token
- `archive_audit_logs`：每月 1 日 04:30 触发，归档超过 1 年的 audit_log 到 R2 + 删除 DB 记录

---

## 6. 数据库迁移（alembic/）

### 6.1 alembic/versions/001_initial_schema.py
**内容**：
- 创建表：tenant, user, role, permission, user_role, role_permission, user_permission_override, refresh_token, audit_log, backup_record
- 创建索引（详见 6.4）
- 创建数据库角色：clothing_app, clothing_bypass

### 6.2 alembic/versions/002_enable_rls.py
**内容**：
- ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY 对所有 TenantScopedModel 表
- CREATE POLICY tenant_isolation 模板
- audit_log 特殊：REVOKE UPDATE, DELETE FROM clothing_app

### 6.3 alembic/versions/003_seed_initial_data.py
**内容**：
- INSERT 默认 tenant
- INSERT 10 个预设 role（按 default_roles.py）
- INSERT 内置 permission 清单
- INSERT role_permission 关联
- 启动时检查并幂等创建第一个 admin 用户（Service 层执行，Alembic 不直接做）

### 6.4 关键索引

| 表 | 索引 | 用途 |
|---|---|---|
| user | UNIQUE (tenant_id, username) | 业务唯一性 |
| user | INDEX (tenant_id, locked_at) WHERE locked_at IS NOT NULL | 锁定用户查询 |
| refresh_token | UNIQUE (jti) | 唯一性 |
| refresh_token | INDEX (user_id, revoked_at) | 用户 token 查询 |
| refresh_token | INDEX (expires_at) WHERE revoked_at IS NULL | 清理任务 |
| audit_log | INDEX (tenant_id, created_at DESC) | 时间倒序查询 |
| audit_log | INDEX (tenant_id, action, created_at DESC) | 按动作筛选 |
| audit_log | INDEX (tenant_id, user_id, created_at DESC) | 按操作人筛选 |
| audit_log | INDEX (created_at) | 归档任务 |
| backup_record | INDEX (retention_until) WHERE r2_key IS NOT NULL | 清理任务 |

---

## 7. 启动检查（main.py lifespan）

### 7.1 启动序列

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 初始化 Sentry
    if settings.SENTRY_DSN:
        sentry_sdk.init(...)

    # 2. 校验 Alembic 版本（已迁移到最新）
    await check_alembic_head()

    # 3. 启动管理员检查（BR-INIT-002）
    async with system_context() as session:
        await ensure_initial_admin(session)

    # 4. 预热 Redis 连接
    await redis.ping()

    yield

    # 关闭时清理
    await engine_app.dispose()
    await engine_bypass.dispose()
    await redis.aclose()
```

### 7.2 ensure_initial_admin 流程

```
1. 查 default tenant 中是否已有 admin role 用户
2. 没有：
   - 生成 16 位随机密码
   - 创建 user(username='admin', password_hash=hash(p), password_must_change=true)
   - 绑定 admin role
   - 写 audit_log("initial_admin_created")
   - print(f"[U01] Initial admin created. Password: {plain_password}")  # 仅 stdout
```

---

## 8. 配置项与环境变量映射

| 配置项 | 环境变量 | 默认值 | 决策来源 |
|---|---|---|---|
| DB pool_size | DB_POOL_SIZE | 5 | Q5=C |
| DB max_overflow | DB_MAX_OVERFLOW | 10 | Q5=C |
| 权限缓存 TTL | PERM_CACHE_TTL_SECONDS | 300 | Q6=B |
| 限流 TTL | RATE_LIMIT_TTL_SECONDS | 900 | Q6=B |
| access_token 过期 | ACCESS_TOKEN_EXPIRE_MINUTES | 30 | Q3=A |
| refresh_token 过期 | REFRESH_TOKEN_EXPIRE_DAYS | 7 | Q3=A |
| bcrypt cost | BCRYPT_ROUNDS | 12 | functional-design BR-PWD-002 |
| 备份保留日 | BACKUP_RETAIN_DAILY_DAYS | 30 | NFR04 |
| 备份保留月 | BACKUP_RETAIN_MONTHLY_MONTHS | 12 | NFR04 |
| audit_log 保留月 | AUDIT_RETAIN_MONTHS | 12 | Q8=B |

---

## 9. 组件依赖关系（U01 内部）

```
core/config (无依赖)
   ↓
core/exceptions, core/errors (无依赖)
   ↓
core/cache (← config)
core/db (← config, exceptions)
   ↓
core/security/auth (← config, cache)
core/security/permissions (← cache)
core/security/rls (← migration only)
   ↓
core/audit (← db, security/permissions)
core/tenancy (← db, exceptions)
core/logging (← config)
   ↓
core/middleware/* (← logging, tenancy, security/auth)
   ↓
modules/auth/repository (← db, models)
modules/auth/domain (← cache, security/auth)
modules/auth/service (← repository, domain, audit, cache)
modules/auth/api (← service, security/permissions, schemas)
   ↓
tasks/backup_tasks (← db, celery_app, R2 client)
tasks/cleanup_tasks (← db, celery_app)
   ↓
main.py (← all)
```

✅ 无循环依赖。

---

## 10. 一致性校验

| 校验 | 结果 |
|---|---|
| 26 个组件全部对应 Application Design 第 1 节项目结构 | ✅ |
| 每个组件有明确职责 | ✅ |
| 依赖关系无循环 | ✅ |
| 索引设计满足查询模式 | ✅ |
| 启动序列覆盖 BR-INIT-001/002/003 | ✅ |
| 配置项与决策一一对应 | ✅ |
| Alembic migration 顺序合理（schema → RLS → seed） | ✅ |
