# U01 代码生成计划（Code Generation Plan）

> 单元：U01 — 认证 + 多租户基础 + 备份框架  
> 阶段：MVP 第 1 个单元（首单元，需建立项目骨架）

---

## 1. 单元上下文

### 1.1 覆盖故事
| 故事 | 阶段 |
|---|---|
| EP01-S01 用户登录 | MVP |
| EP01-S02 修改密码 | MVP |
| EP01-S03 管理员管理用户 | MVP |
| EP01-S04 管理员分配预设角色 | MVP |
| EP01-S07 多租户隔离 | MVP |
| EP01-S08 审计日志查询 | MVP |
| EP10-NFR03 多租户隔离 | MVP |
| EP10-NFR04 备份与恢复（任务体 + 保留策略） | MVP |

### 1.2 单元依赖
- **依赖**：— （根单元，无前置）
- **被依赖**：所有其他单元都依赖 U01

### 1.3 工作区根目录
`e:\work\Pycharm_Projection\eCommerce_v4\`

### 1.4 项目结构（首单元建立骨架）
Greenfield 单 backend 应用 + 单 frontend 应用 + monorepo：
- backend 代码：`backend/`
- frontend 代码：`frontend/`（U01 阶段只创建最小骨架：登录页、AppLayout、authStore）
- 共享配置：仓库根（docker-compose.yml / .env.example / README.md / .github/）
- 文档摘要：`aidlc-docs/construction/U01/code/`

### 1.5 设计文档引用
- functional-design：`aidlc-docs/construction/U01/functional-design/{domain-entities,business-rules,business-logic-model}.md`
- nfr-requirements：`aidlc-docs/construction/U01/nfr-requirements/{nfr-requirements,tech-stack-decisions}.md`
- nfr-design：`aidlc-docs/construction/U01/nfr-design/{nfr-design-patterns,logical-components}.md`
- infrastructure-design：`aidlc-docs/construction/U01/infrastructure-design/{infrastructure-design,deployment-architecture}.md`
- shared-infrastructure：`aidlc-docs/construction/shared-infrastructure.md`

---

## 2. 执行步骤总览

按"项目骨架 → 配置/迁移 → 横切核心 → 业务模块 → 任务 → 测试 → 前端最小骨架 → 部署文件 → 文档摘要"顺序执行。每完成一步标记 `[x]`。

### Step 1 — 项目骨架（仓库根 + 子项目目录）
- [x] 1.1 创建仓库根文件：`.gitignore`、`README.md`、`.env.example`
- [x] 1.2 创建 backend 目录：`backend/`、`backend/app/`、`backend/tests/`、`backend/alembic/`
- [x] 1.3 创建 frontend 目录：`frontend/`、`frontend/src/`、`frontend/public/`
- [x] 1.4 创建 rpa-worker 占位目录 + README
- [x] 1.5 创建 docs 目录 + 部署文档占位

### Step 2 — Backend Python 配置文件
- [x] 2.1 `backend/pyproject.toml`（含 ruff / mypy / pytest 配置）
- [x] 2.2 `backend/requirements.txt`（按 tech-stack-decisions.md 第 2 节，精确版本）
- [x] 2.3 `backend/Dockerfile`（按 deployment-architecture.md 第 2.1 节）
- [x] 2.4 `backend/.dockerignore`
- [x] 2.5 `backend/alembic.ini`
- [x] 2.6 `backend/alembic/env.py`（async 配置）
- [x] 2.7 `backend/alembic/script.py.mako`

### Step 3 — Backend 应用配置（core/config.py）
- [x] 3.1 `backend/app/__init__.py`
- [x] 3.2 `backend/app/core/__init__.py`
- [x] 3.3 `backend/app/core/config.py`（Pydantic Settings，含全部 U01 环境变量）
- [x] 3.4 `backend/app/core/exceptions.py`（11 个异常类）
- [x] 3.5 `backend/app/core/errors.py`（FastAPI 异常处理器，统一 `{code, message, details}` 响应）

### Step 4 — Backend 横切核心（数据库 + 缓存 + 多租户）
- [x] 4.1 `backend/app/core/db.py`（双引擎 engine_app/engine_bypass + Base + TenantScopedModel + before_compile 事件钩子 + get_session 依赖）
- [x] 4.2 `backend/app/core/cache.py`（Redis 异步客户端封装 + CacheClient 类）
- [x] 4.3 `backend/app/core/tenancy.py`（contextvars + system_context 上下文管理器）
- [x] 4.4 `backend/app/core/celery_app.py`（Celery 应用 + Beat 调度配置）

### Step 5 — Backend 横切核心（安全 + 审计 + 状态机 + 附件）
- [x] 5.1 `backend/app/core/security/__init__.py`
- [x] 5.2 `backend/app/core/security/auth.py`（JWT encode/decode + 密码哈希 + 黑名单 revoke_jti/is_revoked）
- [x] 5.3 `backend/app/core/security/permissions.py`（require_permission 装饰器 + check_permission + get_effective_permissions 含 Redis 缓存）
- [x] 5.4 `backend/app/core/security/crypto.py`（AES-256 加解密占位，U12 启用）
- [x] 5.5 `backend/app/core/security/rls.py`（RLS 策略 SQL 模板，给 Alembic 用）
- [x] 5.6 `backend/app/core/audit.py`（@audit 装饰器 + AuditService.log/query + ORM 事件钩子注册器）
- [x] 5.7 `backend/app/core/state_machine.py`（StateMachine 基类 + TransitionRule，给后续单元用）
- [x] 5.8 `backend/app/core/attachment.py`（AttachmentService + R2 boto3 客户端，公私桶分离 + 签名 URL）

### Step 6 — Backend 中间件
- [x] 6.1 `backend/app/core/middleware/__init__.py`
- [x] 6.2 `backend/app/core/middleware/request_id.py`
- [x] 6.3 `backend/app/core/middleware/tenancy.py`（解析 JWT 写 contextvars + Sentry tag + structlog bind）
- [x] 6.4 `backend/app/core/logging.py`（structlog 配置 + 敏感字段 redact）

### Step 7 — Backend 业务模块（auth）
- [x] 7.1 `backend/app/modules/__init__.py`
- [x] 7.2 `backend/app/modules/auth/__init__.py`
- [x] 7.3 `backend/app/modules/auth/models.py`（9 个 ORM 模型：tenant/user/role/permission/user_role/role_permission/user_permission_override/refresh_token/audit_log/backup_record）
- [x] 7.4 `backend/app/modules/auth/schemas.py`（Pydantic 请求/响应 Schema：LoginRequest/TokenPair/UserCreate/UserUpdate/UserOut/RoleAssignRequest/AuditLogQuery/AuditLogEntry 等）
- [x] 7.5 `backend/app/modules/auth/permissions.py`（scope 常量定义）
- [x] 7.6 `backend/app/modules/auth/default_roles.py`（10 个预设角色 + 默认权限矩阵）
- [x] 7.7 `backend/app/modules/auth/repository.py`（6 个 Repository：User/Role/Permission/UserPermissionOverride/RefreshToken/AuditLog）
- [x] 7.8 `backend/app/modules/auth/domain.py`（Permissions 领域对象 + LoginAttemptCounter）
- [x] 7.9 `backend/app/modules/auth/service.py`（AuthService/UserService/PermissionService 实现 BR-AUTH/BR-PWD/BR-PERM 业务规则）
- [x] 7.10 `backend/app/modules/auth/api.py`（13 个 API 端点 router）
- [x] 7.11 `backend/app/modules/auth/exceptions.py`（模块特定异常）

### Step 8 — Backend Celery 任务
- [x] 8.1 `backend/app/tasks/__init__.py`
- [x] 8.2 `backend/app/tasks/backup_tasks.py`（backup_database + cleanup_expired_backups，含 pg_dump subprocess + R2 上传 + autoretry + Sentry capture）
- [x] 8.3 `backend/app/tasks/cleanup_tasks.py`（cleanup_expired_refresh_tokens + archive_audit_logs）

### Step 9 — Backend 入口与启动
- [x] 9.1 `backend/app/main.py`（FastAPI app + lifespan + 中间件链 + Sentry init + Prometheus + 限流 + 错误处理器 + 健康检查端点 /health 和 /ready + 路由挂载 + ensure_initial_admin）

### Step 10 — Alembic 数据库迁移
- [x] 10.1 `backend/alembic/versions/001_u01_initial_schema.py`（创建所有 U01 表 + 索引）
- [x] 10.2 `backend/alembic/versions/002_u01_enable_rls.py`（创建 3 个 PG 角色 + 启用 RLS + 策略 + audit_log REVOKE）
- [x] 10.3 `backend/alembic/versions/003_u01_seed_initial_data.py`（写入 default tenant + 10 个 role + permission 清单 + role_permission 矩阵）
- [x] 10.4 `backend/alembic/init/001_create_roles.sql`（本地 docker-compose 用，创建 PG 角色）

### Step 11 — Backend 测试
- [x] 11.1 `backend/tests/__init__.py`、`backend/tests/conftest.py`（pytest fixtures：pg_db / session / tenant_a / tenant_b）
- [x] 11.2 `backend/tests/unit/test_permissions.py`（权限合并算法 BR-PERM-001 单元测试）
- [x] 11.3 `backend/tests/unit/test_password_policy.py`（BR-PWD-001 校验）
- [x] 11.4 `backend/tests/unit/test_state_machine.py`（StateMachine 基类）
- [x] 11.5 `backend/tests/integration/test_auth_login.py`（登录主路径 + 失败 + 限流 + 锁定）
- [x] 11.6 `backend/tests/integration/test_auth_password.py`（修改密码 + token 失效）
- [x] 11.7 `backend/tests/integration/test_user_management.py`（创建/启用禁用/角色分配）
- [x] 11.8 `backend/tests/integration/test_tenant_isolation.py`（典型实体租户隔离）
- [x] 11.9 `backend/tests/integration/test_rls.py`（PostgreSQL RLS 策略生效）
- [x] 11.10 `backend/tests/integration/test_audit_log.py`（audit_log append-only + 查询）
- [x] 11.11 `backend/tests/api/test_health.py`（/health + /ready）
- [x] 11.12 `backend/tests/api/test_audit_log_api.py`（GET /api/audit-logs）

### Step 12 — Backend 工具脚本
- [x] 12.1 `backend/scripts/restore_backup.py`（半自动恢复脚本 + 验收清单输出）

### Step 13 — Frontend 最小骨架
- [x] 13.1 `frontend/package.json`（React 18 + TypeScript + Ant Design 5 + Vite + React Query + Zustand + Axios + react-router-dom）
- [x] 13.2 `frontend/tsconfig.json`、`frontend/tsconfig.node.json`
- [x] 13.3 `frontend/vite.config.ts`
- [x] 13.4 `frontend/index.html`
- [x] 13.5 `frontend/Dockerfile`
- [x] 13.6 `frontend/nginx.conf`
- [x] 13.7 `frontend/src/main.tsx`、`frontend/src/App.tsx`（React Router + QueryClient + Sentry）
- [x] 13.8 `frontend/src/services/apiClient.ts`（Axios 实例 + JWT 拦截器 + 401 自动 refresh）
- [x] 13.9 `frontend/src/stores/authStore.ts`（Zustand：user/token/permissions/login/logout/hasPermission）
- [x] 13.10 `frontend/src/features/auth/api.ts`（login/refresh/changePassword/getMe）
- [x] 13.11 `frontend/src/features/auth/components/LoginForm.tsx`
- [x] 13.12 `frontend/src/features/auth/components/ChangePasswordForm.tsx`
- [x] 13.13 `frontend/src/components/AppLayout/AppLayout.tsx`（Ant Design Layout + 占位侧边栏）
- [x] 13.14 `frontend/src/components/PermissionGate/PermissionGate.tsx`（守卫组件，按权限隐藏内容）
- [x] 13.15 `frontend/src/pages/LoginPage.tsx`、`frontend/src/pages/HomePage.tsx`、`frontend/src/pages/ChangePasswordPage.tsx`
- [x] 13.16 `frontend/src/types/index.ts`（共享类型）

### Step 14 — 仓库根部署文件
- [x] 14.1 `docker-compose.yml`（按 deployment-architecture.md 第 5 节）
- [x] 14.2 `.env.example`（完整环境变量清单）
- [x] 14.3 `.gitignore`（Python / Node / Docker / IDE）
- [x] 14.4 `README.md`（项目介绍 + 本地启动 + 部署链接）
- [x] 14.5 `.github/workflows/ci.yml`（lint + mypy + pytest + frontend tsc + vitest）
- [x] 14.6 `.github/workflows/migrate.yml`（手动触发的 Alembic 迁移 job）
- [x] 14.7 `.github/workflows/deploy-prod.yml`（main 推送触发，docs 占位 — 实际部署由 Zeabur GitHub 集成自动处理）
- [x] 14.8 `.github/workflows/deploy-staging.yml`
- [x] 14.9 `docs/SECRETS_SETUP.md`（密钥设置指南）
- [x] 14.10 `docs/ZEABUR_SETUP.md`（按 deployment-architecture.md 第 4 节 checklist 整理）

### Step 15 — 文档摘要
- [x] 15.1 `aidlc-docs/construction/U01/code/README.md`（U01 生成的代码文件清单 + 故事覆盖映射）
- [x] 15.2 `aidlc-docs/construction/U01/code/api-endpoints.md`（13 个 U01 API 端点的路径/权限/请求响应摘要）
- [x] 15.3 `aidlc-docs/construction/U01/code/test-coverage.md`（测试用例 → 故事 GWT 映射）

### Step 16 — 完成校验
- [x] 16.1 故事追溯：EP01-S01~S04, S07, S08 + EP10-NFR03/NFR04 全部有代码覆盖
- [x] 16.2 标记 aidlc-state.md U01 Code Generation 完成
- [x] 16.3 在 audit.md 记录完成时间戳

---

## 3. 估算规模与执行策略

| 项 | 规模 |
|---|---|
| 步骤总数 | 16 大步 / 约 100 个子步 |
| 文件数（应用代码） | 约 65 个 Python 文件 + 约 25 个 TypeScript/TSX 文件 + 约 15 个配置/脚本/CI 文件 = **~105 文件** |
| 测试文件 | 12 个测试模块 |
| Alembic 迁移 | 3 个 .py 文件 + 1 个 .sql 文件 |

### 执行策略
- 由于代码量大，按"骨架 → 核心 → 业务 → 测试 → 前端 → 部署 → 文档"分阶段执行
- 每个 Step 完成后内部标记 `[x]`
- **不会一次性把所有代码都倒出来**：用户可在任何 Step 后说"暂停"做局部审查
- 优先保证 backend 可用（Step 1-12），frontend 是最小可登录骨架（Step 13），其他部署/CI 文件最后（Step 14）

### 关键质量门
- 所有 Pydantic v2 严格模式 + 类型注解
- 所有 ORM 异步（asyncpg + SQLAlchemy 2.0 async）
- 所有 Service 方法都用类型注解和 docstring
- 测试覆盖核心规则：BR-AUTH-001/002, BR-TKN-003/004, BR-PERM-001, BR-TENANCY-002/003, BR-AUDIT-002, BR-PWD-001
- pg_dump 备份脚本含错误处理 + Sentry capture

---

## 4. 故事追溯矩阵

| 故事 / NFR | 实施位置 | 测试 |
|---|---|---|
| EP01-S01 用户登录 | modules/auth/{api, service, domain}.py | tests/integration/test_auth_login.py |
| EP01-S02 修改密码 | modules/auth/{api, service}.py | tests/integration/test_auth_password.py |
| EP01-S03 用户管理 | modules/auth/{api, service}.py | tests/integration/test_user_management.py |
| EP01-S04 角色分配 | modules/auth/{api, service}.py + default_roles.py | tests/integration/test_user_management.py |
| EP01-S07 多租户隔离 | core/db.py + core/tenancy.py + 002 RLS migration | tests/integration/test_tenant_isolation.py + test_rls.py |
| EP01-S08 审计日志 | core/audit.py + modules/auth/api.py + 001 schema | tests/integration/test_audit_log.py + test_audit_log_api.py |
| EP10-NFR03 多租户 | 同 S07 | 同 S07 |
| EP10-NFR04 备份 | tasks/backup_tasks.py + scripts/restore_backup.py | tests/unit（部分；恢复演练在 Build & Test 阶段） |

---

## 5. 后续阶段说明

- **测试执行**：本计划生成测试代码但不执行；测试运行放到下一阶段（Build & Test，在 MVP 阶段末或 U01 单元末按 NFR Design 决策每单元跑子集）
- **真正部署**：本计划生成部署文件但不执行 Zeabur 部署；按 deployment-architecture.md 第 4 节 checklist 由用户在 Zeabur 控制台操作
- **数据库实际迁移**：本计划生成 Alembic 脚本但不执行 `alembic upgrade head`；用户在本地或 Zeabur 触发

---

> **生成方式提示**：考虑到 ~105 文件的规模，我会按 Step 顺序生成，**每完成 Step 1-3 后停一下让您审查骨架是否符合预期，再继续**。如果您希望"全部一次性生成完再统一审查"，请在批准时说明。
