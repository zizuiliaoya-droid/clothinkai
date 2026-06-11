# Zeabur 首次部署 Checklist

> 按此清单执行可完成 production / staging 首次部署。详细决策见 `aidlc-docs/construction/U01/infrastructure-design/`。

---

## 准备阶段

- [ ] 域名 `clothinkai.com` 已注册
- [ ] Cloudflare R2 4 个桶已建：`clothing-erp-public` / `clothing-erp-private` / `clothing-erp-credentials` / `clothing-erp-backups`
- [ ] R2 API Token 已生成（限定到 4 个桶 R/W）
- [ ] Sentry 账号 + 2 个项目（`clothing-erp-backend` / `clothing-erp-frontend`）
- [ ] GitHub 仓库 `clothinkai/clothing-erp` 已创建
- [ ] Zeabur 账号 + 已选择 HK 区域

## 创建 production 项目

- [ ] 在 Zeabur 创建项目 `clothing-erp-production`
- [ ] 添加 PostgreSQL 16 插件（1 vCPU / 1GB / 10GB）
- [ ] 添加 Redis 插件（256MB）
- [ ] 在 PG Web 控制台执行 `backend/alembic/init/001_create_roles.sql`，记录 3 个角色的真实密码
- [ ] 在 Zeabur Secrets 添加全部环境变量（详见 `docs/SECRETS_SETUP.md`）

## 数据库迁移

- [ ] 通过 GitHub Actions 手动触发 `migrate.yml`，environment=production
- [ ] 等待 `alembic upgrade head` 成功（应执行 001 schema → 002 RLS → 003 seed）
- [ ] PG 控制台验证：
  - `SELECT count(*) FROM tenant` ≥ 1（default tenant）
  - `SELECT count(*) FROM role WHERE is_system = true` = 10
  - `SELECT count(*) FROM permission` ≥ 30

## 部署 4 个服务

| 服务 | Source | CMD | Replicas |
|---|---|---|---|
| frontend | repo `/frontend` | （Dockerfile 默认） | 1+ |
| backend | repo `/backend` | （Dockerfile 默认 uvicorn） | 1+ |
| celery-worker | repo `/backend` | `celery -A app.core.celery_app worker --concurrency=4 --queues=default,backup` | 1+ |
| celery-beat | repo `/backend` | `celery -A app.core.celery_app beat --pidfile=/tmp/celerybeat.pid` | **必须 1** |

- [ ] 创建 frontend 服务，build args 含 `VITE_API_BASE_URL=https://api.clothinkai.com`
- [ ] 创建 backend 服务，健康检查 `/ready`
- [ ] 创建 celery-worker（CMD 覆盖）
- [ ] 创建 celery-beat（CMD 覆盖，Replicas=1）

## 域名绑定

- [ ] frontend 服务 → `app.clothinkai.com`
- [ ] backend 服务 → `api.clothinkai.com`
- [ ] 域名注册商添加 4 个 CNAME（app / api / staging.app / staging.api → Zeabur 给的目标）
- [ ] 等 TLS 证书签发（约 5-15 分钟）

## 启动管理员

- [ ] 查看 backend 日志，找到首启动打印的 `[U01] Initial admin created. Password: ...`
- [ ] **立即** 把密码记录到密码管理器
- [ ] **立即** 清除 Zeabur 日志中的密码行（避免泄露）
- [ ] 用 admin 登录 `app.clothinkai.com`，强制修改密码
- [ ] 在 audit_log 中确认有 `initial_admin_created` + `password_change` 两条记录

## 健康检查

- [ ] `curl https://api.clothinkai.com/health` → 200
- [ ] `curl https://api.clothinkai.com/ready` → 200，DB + Redis 都 ok
- [ ] Sentry 控制台触发一次测试错误，验证捕获
- [ ] 等到次日 03:30 验证 backup 任务已执行（查 `backup_record` 表）

## staging 部署

重复上述流程，项目命名 `clothing-erp-staging`，子域 `staging.*`。staging 共用 R2 桶但路径加 `staging/` 前缀（备份脚本暂不区分，后续单元增强）。

## 持续部署

后续单元（U02-U18）只需 push main → 自动 redeploy。如有 schema 变更：先跑 `migrate.yml` workflow。
