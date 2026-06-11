# U01 基础设施设计计划（Infrastructure Design Plan）

> 单元：U01 — 认证 + 多租户基础 + 备份框架  
> 范围：把逻辑组件映射到具体基础设施（Zeabur 服务、R2 桶、密钥管理、CI/CD）

---

## 概述

需求文档第 6 节锁定了 Zeabur 6 服务拆分，应用设计已经规划了部署模型，NFR Design 已经定下了双引擎、Sentry、Prometheus、备份链路。本计划聚焦 **U01 阶段需要部署起来**的基础设施细节。

注意：U01 是 MVP 第一个单元，**这次部署会建立"共享基础设施"**（PostgreSQL / Redis / R2 桶 / Sentry 项目 / 域名），后续单元只增加业务能力。

---

## 第一部分：决策问题

### Question 1 — Zeabur 项目结构
GitHub 仓库结构？

A) **单仓 monorepo**：`clothing-erp/` 含 `backend/` `frontend/` `worker/`（采集 Worker 同仓但独立部署）
B) **三仓**：`clothing-erp-backend` / `clothing-erp-frontend` / `clothing-erp-rpa-worker` 分开
C) **二仓**：`clothing-erp` (backend+frontend) + `clothing-erp-rpa-worker`
D) Other

[Answer]: A


### Question 2 — Zeabur 环境策略
环境如何拆？

A) **单 production 项目**（U01 简化，一个 Zeabur project，绑定 production 域名）
B) **production + staging 两个项目**（staging 用子域 staging.app.clothinkai.com）
C) **production + staging + dev 三个项目**
D) Other

[Answer]: B


### Question 3 — PostgreSQL 高可用
Zeabur PostgreSQL 插件配置？

A) **单实例 + 每日 pg_dump 备份**（Zeabur 免费/低价位足够，RPO 24h 满足需求）
B) **Zeabur 提供的高可用 PG 套餐**（如有，更贵但 RPO 更短）
C) **外部 PG 服务**（Neon / Supabase / Aiven），Zeabur 只跑应用
D) Other

[Answer]: A


### Question 4 — Redis 配置
Zeabur Redis 插件容量？

A) **256MB**（足够 U01-V1，权限缓存 + 限流 + 黑名单 + Celery broker）
B) **512MB**（留余量）
C) **1GB**（V2 后报表预聚合需要）
D) Other

[Answer]: A


### Question 5 — R2 桶规划
Cloudflare R2 桶？

A) **统一项目下 4 个桶**：`clothing-erp-public` / `clothing-erp-private` / `clothing-erp-credentials` / `clothing-erp-backups`
B) **per-tenant 桶**（按租户拆桶）
C) **2 个桶**：`clothing-erp-public` + `clothing-erp-internal`（私有 + 凭据 + 备份合并）
D) Other

[Answer]: A


### Question 6 — JWT Secret 管理
JWT_SECRET 怎么管？

A) **Zeabur Secrets** 注入到 backend / celery-worker / celery-beat 三个服务
B) **生成一次后人工存于密码管理器**，部署时复制粘贴到 Zeabur
C) Other

[Answer]: A


### Question 7 — AES Master Key（U12 起用，U01 占位）
AES master key（凭据加密用）的管理？

A) **U01 阶段先在 Zeabur Secrets 注入**（与 JWT_SECRET 同等保护），U12 启用时直接用
B) **U01 阶段不注入**（U12 才需要），先空值占位
C) **从 P1 开始改用 KMS（Cloudflare / 自建）**，U01 用 Secrets 占位
D) Other

[Answer]: C


### Question 8 — DNS 与 TLS
域名 + 证书？

A) **自动**：Zeabur 自动签发 Let's Encrypt 证书；DNS 在域名商加 CNAME 指 Zeabur
B) **半自动**：DNS 用 Cloudflare 代理（橙云）+ Zeabur 关闭其证书改用 Cloudflare 终止 TLS
C) Other

[Answer]: A


### Question 9 — CORS 与子域
前后端域名最终方案？

A) **app.clothinkai.com（前端）+ api.clothinkai.com（后端）**，CORS 允许 app（含 staging：staging.app.clothinkai.com）
B) **同域 + 路径分流**：`app.clothinkai.com/api/*` 走后端
C) Other

[Answer]: A


### Question 10 — CI/CD 触发模式
GitHub → Zeabur 部署触发？

A) **main 推送自动部署 production**，staging 用 PR 预览或独立 staging 分支
B) **main 推送部署 staging，手动批准后 promote 到 production**
C) **打 git tag（v0.1.0 / v1.0.0）才部署 production**
D) Other

[Answer]: A


### Question 11 — Alembic 迁移在部署中的执行时机
schema migration 何时跑？

A) **backend 容器启动时自动执行**（lifespan 内 `alembic upgrade head`）
B) **专用 migration job**（Zeabur 一次性 job，部署前手动触发）
C) **CI 中跑**（GitHub Actions 跑 migration，再部署应用）
D) Other

[Answer]: B


### Question 12 — Celery Worker 与 Beat 共置 vs 分开
Celery Worker 和 Beat 部署策略？

A) **分开两个 Zeabur 服务**（celery-worker + celery-beat），符合需求第 6.2 节
B) **共置一个进程**（用 `celery -B` 同时跑 Beat）
C) Other

[Answer]: A


### Question 13 — pg_dump 备份目标桶
pg_dump 上传到 R2 哪个桶 / 路径？

A) **`clothing-erp-backups/` 桶下**：
  - `daily/{YYYY-MM-DD}/daily-{YYYY-MM-DD}.tar.gz`
  - `monthly/{YYYY-MM}/monthly-{YYYY-MM}.tar.gz`
  - `audit-archive/{tenant_id}/{YYYY-MM}.jsonl.gz`
B) **不分子目录**，靠 backup_record 表索引
C) Other

[Answer]: A


### Question 14 — Sentry 项目结构
Sentry 项目划分？

A) **2 个项目**：`clothing-erp-backend` + `clothing-erp-frontend`，环境用 tag（production/staging）区分
B) **4 个项目**（backend prod / backend staging / frontend prod / frontend staging）
C) **1 个项目**，靠 tag 区分
D) Other

[Answer]: A


### Question 15 — 健康检查路径在 Zeabur 配置
Zeabur 容器健康检查端点配置？

A) **liveness=/health（10s 间隔），readiness=/ready（30s 间隔）**
B) **仅配 readiness=/ready**（Zeabur 是否支持双探针待确认）
C) Other

[Answer]: B

