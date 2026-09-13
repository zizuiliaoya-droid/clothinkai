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

> ⚠️ Zeabur 已废弃共享集群。创建项目时不能再用 `hkg1` 这类区域码，
> 必须先租一台服务器（`rentServer`），再用 `server-XXXXXXXX` 作为项目 region。
> 腾讯云服务器不支持退款，规格需一次选对。

## 创建 production 项目

- [ ] 在 Zeabur 创建项目 `clothing-erp-production`
- [ ] 添加 PostgreSQL 插件
- [ ] 添加 Redis 插件
- [ ] 创建业务库：`CREATE DATABASE clothing_erp;`
- [ ] **以超级用户预建扩展：`CREATE EXTENSION IF NOT EXISTS pg_trgm;`**
      migration 004/005/006 都会执行该语句，但 `clothing_bypass` 无 database 级 CREATE 权限，
      不预建会 `permission denied to create extension`；且 alembic 单事务执行，
      004 失败会把 001-003 一起回滚，现象是「一张表都没建出来」。
- [ ] 在 PG 控制台执行 `backend/alembic/init/001_create_roles.sql`，记录 3 个角色的真实密码
- [ ] `GRANT CREATE ON SCHEMA public TO clothing_bypass;`（PG15+ 默认不授予 public schema 的 CREATE）
- [ ] 在 Zeabur Secrets 添加全部环境变量（详见 `docs/SECRETS_SETUP.md`）

## 数据库迁移

生产实际采用的方式：backend 启动命令前置 `alembic upgrade head`（幂等），随服务启动自动迁移。
`migrate.yml` workflow 作为手动补跑通道保留。

- [ ] 确认 backend 首次启动日志里 alembic 从 `001_u01_initial_schema` 一路执行到 head
- [ ] PG 控制台验证（2026-09-13 实测基线）：
  - `SELECT count(*) FROM information_schema.tables WHERE table_schema='public'` = 52
  - `SELECT version_num FROM alembic_version` = `031_promo_payment_qr`
  - `SELECT count(*) FROM tenant` = 1（default tenant）
  - `SELECT count(*) FROM role WHERE is_system = true` = 11
  - `SELECT count(*) FROM permission` = 98

## 部署 4 个服务

| 服务 | Source | CMD | Replicas |
|---|---|---|---|
| frontend | repo `/frontend` | （Dockerfile 默认） | 1+ |
| backend | repo `/backend` | （Dockerfile 默认 uvicorn） | 1+ |
| celery-worker | repo `/backend` | `celery -A app.core.celery_app worker --concurrency=2 --queues=default,backup,crawler,report` | 1+ |
| celery-beat | repo `/backend` | `celery -A app.core.celery_app beat --pidfile=/tmp/celerybeat.pid --schedule=/tmp/celerybeat-schedule` | **必须 1** |

> ⚠️ 队列必须写全 `default,backup,crawler,report`。`crawler`（U13 采集）和 `report`（U14 报表）
> 是后续单元新增的，若只监听 `default,backup`，这两类任务会永远堆在队列里没有消费者。

- [ ] 创建 frontend 服务，环境变量含 `VITE_API_BASE_URL=<后端域名>`（Zeabur 会自动注入为构建期 ARG）
      并**验证产物**：取 `index.html` 引用的 `/assets/index-*.js`，grep 其中的 API 域名。
      vite 产物名是内容哈希，**哈希没变就说明注入没生效**。
      注意 `frontend/Dockerfile` 的 `ARG` 不能带默认值，否则随后的 `ENV` 会覆盖 Zeabur 的注入值。
- [ ] 创建 backend 服务，健康检查 `/ready`，启动命令前置 `alembic upgrade head`
- [ ] 创建 celery-worker（CMD 覆盖）
- [ ] 创建 celery-beat（CMD 覆盖，Replicas=1）

> ⚠️ 若通过 API 的 `deployFromSpecification` 部署，它会覆盖 spec 中的 `command`/`args`。
> 必须在部署**之后**重新下发 `updateServiceStartup`，再 `redeployService` 才生效。
> 否则容器退回 Dockerfile 默认 CMD —— backend 不会跑迁移，两个 celery 服务会错误地起 uvicorn。
>
> 好消息：**git trigger 触发的自动部署会保留 `command`/`args`**（2026-09-13 实测验证）。
> 所以这个坑只在用 API 手工下 spec 时出现，日常 push main 的自动部署不受影响。

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
- [ ] `POST /api/auth/login` → 200 且返回 `must_change_password: true`
- [ ] `GET /api/auth/me` 带 token → 200（若 500，说明 `clothing_app` 缺表权限）
- [ ] 在 celery-worker 容器执行 `celery -A app.core.celery_app inspect active_queues`，
      确认 `default` / `backup` / `crawler` / `report` 四个队列都在监听
- [ ] celery-beat 日志出现 `beat: Starting...` 且 broker 指向 redis db1
- [ ] Sentry 控制台触发一次测试错误，验证捕获
- [ ] 等到次日 03:30 验证 backup 任务已执行（查 `backup_record` 表）

## staging 部署

重复上述流程，项目命名 `clothing-erp-staging`，子域 `staging.*`。staging 共用 R2 桶但路径加 `staging/` 前缀（备份脚本暂不区分，后续单元增强）。

## 持续部署

后续单元只需 push main → 自动 redeploy（schema 变更由 backend 启动时的 `alembic upgrade head` 处理）。

> ⚠️ 自动部署依赖每个 service 上的 **git trigger**（`updateGitTrigger`，绑定 repoID + 分支）。
> 通过 API 的 `deployFromSpecification` 部署**不会**自动建立 git trigger，只是拉一次代码，
> 必须额外调用 `updateGitTrigger` 才有推送即部署。
>
> 排查「推了代码但线上没更新」时，查 Zeabur 侧而不是 GitHub 侧：
> ```graphql
> { service(_id: "<serviceID>") {
>     gitTrigger(environmentID: "<envID>") { provider repoID branchName repoURL } } }
> ```
> 注意：Zeabur 用的是 **GitHub App**，push 事件经 App installation 投递，
> 仓库上没有 per-repo webhook。`GET /repos/{owner}/{repo}/hooks` 返回 0 是正常的，
> 不能用它判断联动是否存在。

## 灾难恢复提示

Zeabur 专用服务器订阅终止会释放服务器，并连带删除其上的项目与数据库数据卷
（2026-09-13 曾因此丢失整套生产环境，表现为域名在边缘网关返回 404）。

- 数据卷不在 Zeabur 的备份范围内，需自行落地异地备份（R2 独立，不受影响）
- 域名释放回公共池后可以重新申请，只要没被他人占用就能拿回同一个 `*.zeabur.app`
- 完整重建步骤与踩坑记录见本地 `deploy-secrets.local.md`（gitignore，未入库）
