# 密钥设置指南

> 本文档说明如何为 production / staging 准备所需的密钥与凭据。所有密钥**禁止**写入仓库。

---

## 1. 必须准备的密钥清单

| 密钥名 | 生成方式 | 用途 |
|---|---|---|
| `JWT_SECRET` | `openssl rand -hex 32` | JWT 签名密钥（256 位） |
| `CREDENTIAL_MASTER_KEY` | `openssl rand -base64 32` | AES-256 master key（U12 启用，U01 占位） |
| 数据库角色密码 ×3 | `openssl rand -base64 24` | clothing_app / clothing_bypass / clothing_archiver |
| `R2_ACCESS_KEY_ID` + `R2_SECRET_ACCESS_KEY` | Cloudflare R2 控制台 | 限定到 4 个桶的 R/W |
| `SENTRY_DSN_BACKEND` | Sentry 项目设置 | 后端异常上报 |
| `SENTRY_DSN_FRONTEND` | Sentry 项目设置 | 前端异常上报 |

## 2. Zeabur Secrets 注入

每个 Zeabur 服务配置环境变量：

### backend / celery-worker / celery-beat
- ENVIRONMENT
- DATABASE_URL_APP / DATABASE_URL_BYPASS / DATABASE_URL_SYNC / DATABASE_URL_ARCHIVER
- REDIS_URL_CACHE / REDIS_URL_CELERY_BROKER / REDIS_URL_CELERY_BACKEND
- JWT_SECRET / CREDENTIAL_MASTER_KEY
- R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
- R2_BUCKET_*（按 production 实际桶名）
- SENTRY_DSN_BACKEND
- CORS_ALLOWED_ORIGINS

### frontend（构建时注入）
- VITE_API_BASE_URL（如 `https://api.clothinkai.com`）
- VITE_SENTRY_DSN（即 SENTRY_DSN_FRONTEND）

## 3. GitHub Actions Secrets

为 `migrate.yml` workflow 设置 GitHub Environment secrets：

| Environment: production | Environment: staging |
|---|---|
| DATABASE_URL_APP | DATABASE_URL_APP |
| DATABASE_URL_BYPASS | DATABASE_URL_BYPASS |
| DATABASE_URL_SYNC | DATABASE_URL_SYNC |
| JWT_SECRET | JWT_SECRET |
| CREDENTIAL_MASTER_KEY | CREDENTIAL_MASTER_KEY |

## 4. PostgreSQL 角色密码注入

部署 PG 后，在 Zeabur PostgreSQL Web 控制台执行 `backend/alembic/init/001_create_roles.sql`，**记得先把 `app_password_change_me` 等占位字符串替换为真实密码**：

```sql
-- 在 SQL 控制台先执行（替换为生成的密码）
ALTER ROLE clothing_app WITH PASSWORD '<your_app_password>';
ALTER ROLE clothing_bypass WITH PASSWORD '<your_bypass_password>';
ALTER ROLE clothing_archiver WITH PASSWORD '<your_archiver_password>';
```

记录密码到密码管理器，并更新 Zeabur 中的 `DATABASE_URL_*` 环境变量。

## 5. Sentry PII 防护

- `send_default_pii=False`（已在代码中）
- `before_send` 自动过滤 password / token / authorization 字段（已在代码中）
- **不要**在自定义 Sentry tag 中加入用户名 / email
- 如需排查特定用户问题，使用 audit_log 而非 Sentry

## 6. 密钥轮换（V1+ KMS 升级）

U01 阶段：所有密钥手动管理。  
P1+：CREDENTIAL_MASTER_KEY 切换到 Cloudflare KMS / HashiCorp Vault，每 90 天自动轮换。
