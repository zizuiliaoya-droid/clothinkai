# U01 基础设施设计（Infrastructure Design）

> 把逻辑组件映射到具体基础设施服务。U01 是首个单元，建立"共享基础设施"基线。

---

## 1. 服务拓扑总览

```
┌─────────────────────── Internet ───────────────────────┐
│                                                          │
│  app.clothinkai.com           api.clothinkai.com         │
│  staging.app.clothinkai.com   staging.api.clothinkai.com │
│         │                            │                   │
│  [DNS CNAME @ 域名商]           [DNS CNAME @ 域名商]      │
└─────────┼────────────────────────────┼──────────────────┘
          ▼                            ▼
   ┌─────────────────────────────────────────────┐
   │              Zeabur (HK Region)              │
   │  ┌─────────────────────────────────────┐    │
   │  │  Project: clothing-erp-production   │    │
   │  │  ┌──────────┐  ┌──────────────┐    │    │
   │  │  │ frontend │  │   backend    │    │    │
   │  │  │  (React) │  │   (FastAPI)  │    │    │
   │  │  └──────────┘  └──────┬───────┘    │    │
   │  │                        │             │    │
   │  │  ┌──────────────┐  ┌──┴───────┐    │    │
   │  │  │ celery-beat  │  │ celery-  │    │    │
   │  │  │              │  │ worker   │    │    │
   │  │  └──────┬───────┘  └────┬─────┘    │    │
   │  │         │                │           │    │
   │  │  ┌──────▼────────────────▼──────┐  │    │
   │  │  │  PostgreSQL 16 (插件，单实例) │  │    │
   │  │  └───────────────────────────────┘  │    │
   │  │  ┌───────────────────────────────┐  │    │
   │  │  │  Redis 7 (256MB 插件)         │  │    │
   │  │  └───────────────────────────────┘  │    │
   │  └─────────────────────────────────────┘    │
   │                                              │
   │  ┌─────────────────────────────────────┐    │
   │  │  Project: clothing-erp-staging       │    │
   │  │  （结构相同，规格降级）              │    │
   │  └─────────────────────────────────────┘    │
   └────────┬─────────────────────────────────────┘
            │
   ┌────────▼─────────┐    ┌──────────────────┐
   │  Cloudflare R2   │    │     Sentry       │
   │  4 buckets       │    │  2 projects      │
   │  - public        │    │  - backend       │
   │  - private       │    │  - frontend      │
   │  - credentials   │    │  (env tag 区分)  │
   │  - backups       │    └──────────────────┘
   └──────────────────┘

   ┌──────────────────────────────────────┐
   │  外部采集 Worker（U13 启用，U01 不部署）│
   │  独立的 Windows VM / Docker 主机     │
   │  通过 HTTPS pull api.clothinkai.com  │
   └──────────────────────────────────────┘
```

---

## 2. Zeabur 服务规格

### 2.1 Production（clothing-erp-production）

| 服务名 | 类型 | 镜像/构建 | 规格（建议起步） | 健康检查 | 实例数 |
|---|---|---|---|---|---|
| frontend | Web | `frontend/Dockerfile` 静态文件 + nginx | 0.5 vCPU / 256MB | TCP 80 | 1 |
| backend | Web | `backend/Dockerfile` (uvicorn) | 1 vCPU / 1GB | HTTP `/ready` 30s | 1（U01）→ 2+（V1+） |
| celery-worker | Worker | `backend/Dockerfile` (CMD: celery worker) | 1 vCPU / 1GB | 进程存活 | 1 |
| celery-beat | Cron | `backend/Dockerfile` (CMD: celery beat) | 0.25 vCPU / 256MB | 进程存活 | 1（必须单实例） |
| postgres | 插件 | Zeabur PostgreSQL 16 单实例 | 1 vCPU / 1GB / 10GB SSD | Zeabur 内置 | 1 |
| redis | 插件 | Zeabur Redis 7 | 256MB | Zeabur 内置 | 1 |

### 2.2 Staging（clothing-erp-staging）

同上结构，规格减半（如 backend 0.5 vCPU / 512MB），共享 R2 桶但子路径加 `staging/` 前缀。

### 2.3 健康检查路径（Q15=B，仅 readiness）

| 服务 | 端点 | 间隔 | 启动延迟 | 失败次数 |
|---|---|---|---|---|
| backend | GET `/ready` | 30s | 60s | 3 → 重启 |
| frontend | TCP 80 | 30s | 10s | 3 → 重启 |
| celery-worker | `celery -A app inspect ping`（命令） | 60s | 30s | 3 → 重启 |
| celery-beat | 进程存活 | 60s | 30s | 3 → 重启 |

> Zeabur 文档目前以 readiness 单端点为主流，liveness 端点保留在代码侧供未来切换 Kubernetes 等平台时复用。

---

## 3. PostgreSQL 配置

### 3.1 实例规格（Q3=A 单实例）
- Zeabur PostgreSQL 16 插件，初期 1 vCPU / 1GB / 10GB SSD
- 单实例（无主从），靠每日 pg_dump 备份保证 RPO ≤ 24h

### 3.2 数据库角色（按 NFR Design 双引擎）

```sql
-- migrations/001_create_roles.sql（在 Zeabur PG 控制台手动跑一次）

CREATE ROLE clothing_app NOINHERIT LOGIN PASSWORD :app_password;
CREATE ROLE clothing_bypass BYPASSRLS NOINHERIT LOGIN PASSWORD :bypass_password;
CREATE ROLE clothing_archiver NOINHERIT LOGIN PASSWORD :archiver_password;  -- audit_log 归档专用

GRANT CONNECT ON DATABASE clothing_erp TO clothing_app, clothing_bypass, clothing_archiver;

-- 后续 Alembic migration 给表授权
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO clothing_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO clothing_bypass;
-- audit_log 例外：clothing_app 仅 INSERT + SELECT
REVOKE UPDATE, DELETE ON audit_log FROM clothing_app;
-- clothing_archiver 拥有 audit_log 的 DELETE 权限（用于归档清理）
GRANT SELECT, DELETE ON audit_log TO clothing_archiver;
```

### 3.3 连接策略

| 客户端 | 角色 | 连接 URL（环境变量） |
|---|---|---|
| backend (engine_app) | clothing_app | DATABASE_URL_APP |
| backend (engine_bypass) | clothing_bypass | DATABASE_URL_BYPASS |
| celery-worker | 同上两个引擎 | 同上 |
| pg_dump 任务（celery-worker） | postgres superuser | DATABASE_URL_SYNC（仅备份用） |
| audit_log 归档任务 | clothing_archiver | DATABASE_URL_ARCHIVER |

### 3.4 备份策略
- 每日 03:00 由 celery-worker 执行 pg_dump（subprocess）
- 上传 R2 `clothing-erp-backups/daily/`
- 保留：30 天每日 + 1 年每月

---

## 4. Redis 配置

### 4.1 容量（Q4=A 256MB）
- U01-V1 内存预算：< 100MB（权限缓存 + 限流 + 黑名单 + Celery 队列）
- 预留 156MB 给 V2+ 扩展
- 监控：内存使用率 > 70% 触发预警

### 4.2 库分片
按 Redis DB 编号隔离用途：

| DB | 用途 | TTL 模式 |
|---|---|---|
| 0 | 应用缓存（权限 / 限流 / 登录失败计数） | 短（5m-15m） |
| 1 | Celery broker | 任务消费即清 |
| 2 | Celery result backend | 默认 1 天 |
| 3 | JWT 黑名单 | 与 access_token 同寿 |

环境变量：
- `REDIS_URL_CACHE = redis://...:6379/0`
- `REDIS_URL_CELERY_BROKER = redis://...:6379/1`
- `REDIS_URL_CELERY_BACKEND = redis://...:6379/2`

### 4.3 持久化
Zeabur Redis 默认 RDB（每日快照）+ AOF（每秒）。U01 阶段不调整。即使 Redis 数据全失，应用回退到 DB 查询不阻断业务（性能下降但功能可用）。

---

## 5. Cloudflare R2 配置

### 5.1 4 个桶（Q5=A）

| 桶 | 用途 | 公开/私有 | 路径示例（含 tenant 隔离） |
|---|---|---|---|
| `clothing-erp-public` | 商品图、设计稿预览 | 公开（CDN） | `{tenant_id}/styles/{style_id}/{filename}` |
| `clothing-erp-private` | 付款截图、买家秀、制版文件 | 私有（签名 URL，TTL 15min） | `{tenant_id}/settlements/{id}/proof/{uuid}.png` |
| `clothing-erp-credentials` | 加密凭据备份（U12 启用） | 私有，仅后端服务 access | `{tenant_id}/credentials/{credential_id}/{version}.bin` |
| `clothing-erp-backups` | DB 备份 + audit_log 归档 | 私有，仅后端服务 access | 详见 5.2 |

### 5.2 backups 桶子目录结构（Q13=A）

```
clothing-erp-backups/
├── daily/
│   └── {YYYY-MM-DD}/
│       └── daily-{YYYY-MM-DD}.tar.gz       # pg_dump + R2 凭据元数据 + 配置导出
├── monthly/
│   └── {YYYY-MM}/
│       └── monthly-{YYYY-MM}.tar.gz
└── audit-archive/
    └── {tenant_id}/
        └── {YYYY-MM}.jsonl.gz
```

### 5.3 staging 共享桶子路径

staging 共用同一桶但加 `staging/` 前缀：
- `clothing-erp-public/staging/{tenant_id}/...`
- `clothing-erp-backups/staging/daily/...`

### 5.4 R2 访问凭据
- 使用 R2 API Token（不是 root key），权限范围限定到对应 4 个桶
- Token 通过 Zeabur Secrets 注入：`R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`
- backend / celery-worker / celery-beat 三服务都需要

---

## 6. 密钥与配置管理

### 6.1 Zeabur Secrets（Q6=A, Q7=C）

注入到 backend / celery-worker / celery-beat 三个服务：

| Secret | 来源 | U01 必需 | 用途 |
|---|---|---|---|
| JWT_SECRET | 一次性生成 256-bit hex（`openssl rand -hex 32`） | ✅ | JWT 签名 |
| CREDENTIAL_MASTER_KEY | 占位（U01 注入空字符串或固定测试值） | ⚠️ U12 启用，U01 占位 | 凭据 AES 加密 master key |
| DATABASE_URL_APP | Zeabur 自动生成 + 修改 role | ✅ | 应用主连接 |
| DATABASE_URL_BYPASS | 同上不同 role | ✅ | 跨租户连接 |
| DATABASE_URL_SYNC | 同上但 driver=psycopg2 | ✅ | pg_dump 用 |
| DATABASE_URL_ARCHIVER | 归档专用 role | ✅ | audit_log 归档 |
| REDIS_URL_CACHE | Zeabur Redis 自动注入 | ✅ | 缓存 |
| REDIS_URL_CELERY_BROKER | 同上 db=1 | ✅ | Celery |
| REDIS_URL_CELERY_BACKEND | 同上 db=2 | ✅ | Celery |
| R2_ENDPOINT_URL | Cloudflare 控制台 | ✅ | R2 |
| R2_ACCESS_KEY_ID | Cloudflare API Token | ✅ | R2 |
| R2_SECRET_ACCESS_KEY | Cloudflare API Token | ✅ | R2 |
| SENTRY_DSN_BACKEND | Sentry 控制台 | ✅ | 错误追踪 |
| CORS_ALLOWED_ORIGINS | 静态配置 | ✅ | CORS |
| ENVIRONMENT | "production" / "staging" | ✅ | Sentry tag、行为差异 |

### 6.2 P1+ 升级路径（Q7=C）
- U12 启用真正的凭据加密时，CREDENTIAL_MASTER_KEY 切换为从 Cloudflare KMS（或自建 HashiCorp Vault）取
- Zeabur Secrets 改存 KMS 客户端凭据，应用启动时从 KMS 拉 master key 到内存

---

## 7. DNS 与 TLS（Q8=A, Q9=A）

### 7.1 域名分配

| 子域 | 指向 | 环境 |
|---|---|---|
| `app.clothinkai.com` | Zeabur frontend (production) | prod 前端 |
| `api.clothinkai.com` | Zeabur backend (production) | prod 后端 |
| `staging.app.clothinkai.com` | Zeabur frontend (staging) | staging 前端 |
| `staging.api.clothinkai.com` | Zeabur backend (staging) | staging 后端 |

### 7.2 DNS 配置（域名注册商）

| 类型 | 主机 | 记录值 | 备注 |
|---|---|---|---|
| CNAME | app | `cname.zeabur.app` | Zeabur 给的目标 |
| CNAME | api | `cname.zeabur.app` | 同上 |
| CNAME | staging.app | `cname.zeabur.app` | 同上 |
| CNAME | staging.api | `cname.zeabur.app` | 同上 |

### 7.3 TLS
- Zeabur 自动签发 Let's Encrypt 证书（每 90 天自动续期）
- HSTS 头由后端代码层添加（`Strict-Transport-Security: max-age=31536000`）

### 7.4 CORS

```python
# 通过 settings.CORS_ALLOWED_ORIGINS 配置
CORS_ALLOWED_ORIGINS = [
    "https://app.clothinkai.com",          # 生产
    "https://staging.app.clothinkai.com",  # staging
    "http://localhost:5173",                # 本地开发（仅 dev 配置）
]
```

---

## 8. CI/CD（Q10=A, Q11=B）

### 8.1 GitHub Actions 流程

```
.github/workflows/
├── ci.yml             # 所有 PR + push 跑 lint/test
├── deploy-staging.yml # PR 预览或 staging 分支
└── deploy-prod.yml    # main 分支推送
```

### 8.2 ci.yml 关键步骤
- backend: ruff + mypy + pytest（含覆盖率 ≥ 80%）
- frontend: eslint + tsc + vitest
- 失败阻塞合并

### 8.3 部署流程

| 触发 | 动作 |
|---|---|
| PR 推送 | CI 跑测试；部署 staging 预览（Zeabur PR 预览功能） |
| `staging` 分支推送 | 部署到 staging Zeabur 项目 |
| `main` 分支推送 | 部署到 production Zeabur 项目（Q10=A 自动） |
| 打 tag `v*` | （可选）创建 GitHub Release，作为审计留痕 |

### 8.4 Migration job（Q11=B）

部署前手动触发的专用 job，避免 backend 启动竞态：

```yaml
# .github/workflows/migrate.yml
on:
  workflow_dispatch:
    inputs:
      environment:
        type: choice
        options: [staging, production]
jobs:
  migrate:
    steps:
      - run: alembic upgrade head
        env:
          DATABASE_URL: ${{ secrets[format('DATABASE_URL_{0}', inputs.environment)] }}
```

部署前的标准操作顺序：
1. `gh workflow run migrate.yml -f environment=production`
2. 等 migration 成功
3. push main → 自动部署 backend / celery-worker / celery-beat

> 优点：migration 失败不会让应用启动失败；多实例部署时不会竞态执行 migration。

---

## 9. Sentry 配置（Q14=A）

### 9.1 项目结构

| Sentry 项目 | DSN 用途 | 环境 tag |
|---|---|---|
| `clothing-erp-backend` | backend / celery-worker / celery-beat | `environment=production / staging` |
| `clothing-erp-frontend` | frontend SPA | `environment=production / staging` |

### 9.2 Backend 集成
- `sentry_sdk.init(dsn, environment=settings.ENVIRONMENT, ...)`
- FastApiIntegration + SqlalchemyIntegration + CeleryIntegration
- traces_sample_rate: prod=0.1, staging=1.0
- 不发送 PII（详见 nfr-design 第 7 节）
- before_send 过滤敏感字段

### 9.3 Frontend 集成
- 单独 SDK，traces_sample_rate=0.1
- Source Maps 上传（CI 步骤）

### 9.4 告警规则
- backend：连续 10 次 500 错误 → Slack/邮件告警
- backup_failed_terminal 事件 → 立即告警（U01 唯一关键告警）

---

## 10. 环境变量映射汇总

环境变量在 Zeabur 服务级配置：

| 变量 | backend | celery-worker | celery-beat | frontend |
|---|---|---|---|---|
| ENVIRONMENT | ✅ | ✅ | ✅ | ✅（构建时注入） |
| DATABASE_URL_APP | ✅ | ✅ | ✅ | — |
| DATABASE_URL_BYPASS | ✅ | ✅ | ✅ | — |
| DATABASE_URL_SYNC | — | ✅（备份用） | — | — |
| DATABASE_URL_ARCHIVER | — | ✅（归档用） | — | — |
| REDIS_URL_* | ✅ | ✅ | ✅ | — |
| JWT_SECRET | ✅ | ✅ | — | — |
| CREDENTIAL_MASTER_KEY | ✅ | ✅ | — | — |
| R2_* | ✅ | ✅ | — | — |
| SENTRY_DSN_BACKEND | ✅ | ✅ | ✅ | — |
| SENTRY_DSN_FRONTEND | — | — | — | ✅ |
| CORS_ALLOWED_ORIGINS | ✅ | — | — | — |
| BCRYPT_ROUNDS | ✅ | — | — | — |
| ACCESS_TOKEN_EXPIRE_MINUTES | ✅ | — | — | — |
| REFRESH_TOKEN_EXPIRE_DAYS | ✅ | — | — | — |
| BACKUP_RETAIN_DAILY_DAYS | — | ✅ | ✅ | — |
| AUDIT_RETAIN_MONTHS | — | ✅ | ✅ | — |
| INITIAL_ADMIN_USERNAME | ✅ | — | — | — |
| VITE_API_BASE_URL | — | — | — | ✅ |

---

## 11. 一致性校验

| 校验 | 结果 |
|---|---|
| 15 个决策全部映射到具体配置 | ✅ |
| 与需求第 6 节 Zeabur 6 服务一致 | ✅（frontend/backend/celery-worker/celery-beat/postgres/redis）|
| 与 NFR Design 双引擎模式一致 | ✅（3 个 PG 角色 + 4 个连接 URL） |
| 与 NFR Requirements 容量预算一致 | ✅（pool_size=5/Redis 256MB） |
| 与 functional-design 备份保留策略一致 | ✅（30d/1y） |
| 不依赖 U07 企微 / U12 凭据完成 | ✅（CREDENTIAL_MASTER_KEY 占位） |
| Sentry 项目数与 Q14 决策一致 | ✅（2 个项目，环境 tag） |
| CORS 同时覆盖 prod / staging / 本地 dev | ✅ |
