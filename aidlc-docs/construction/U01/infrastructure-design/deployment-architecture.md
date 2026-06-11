# U01 部署架构（Deployment Architecture）

> 详细的部署流程、Dockerfile、Zeabur 服务配置、上线 checklist。

---

## 1. 仓库结构（Q1=A monorepo）

```
clothing-erp/                              # GitHub: clothinkai/clothing-erp
├── backend/
│   ├── app/                               # FastAPI 应用代码
│   ├── alembic/                           # 数据库迁移
│   ├── tests/
│   ├── pyproject.toml
│   ├── requirements.txt
│   └── Dockerfile                         # 同时被 backend / celery-worker / celery-beat 使用
├── frontend/
│   ├── src/
│   ├── public/
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile                         # 静态构建 + nginx
├── rpa-worker/                            # U13 启用，U01 占位空目录
│   └── README.md
├── docker-compose.yml                     # 本地开发：postgres + redis + backend + worker + beat
├── .github/
│   └── workflows/
│       ├── ci.yml
│       ├── migrate.yml
│       ├── deploy-staging.yml
│       └── deploy-prod.yml
├── docs/                                  # 部署文档
├── .env.example
├── .gitignore
└── README.md
```

---

## 2. Dockerfile 设计

### 2.1 backend/Dockerfile

```dockerfile
FROM python:3.12-slim AS builder
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --user -r requirements.txt

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PATH=/root/.local/bin:$PATH

# pg_dump 客户端（备份任务用）
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY backend/ .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/ready || exit 1
```

### 2.2 frontend/Dockerfile

```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=30s CMD curl -f http://localhost/ || exit 1
```

### 2.3 frontend/nginx.conf

```nginx
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / { try_files $uri $uri/ /index.html; }

    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
}
```

---

## 3. Zeabur 服务配置（per service）

### 3.1 frontend
| 配置项 | 值 |
|---|---|
| Source | GitHub `clothinkai/clothing-erp`，root `/frontend` |
| Build | Dockerfile |
| Port | 80 |
| Domain | app.clothinkai.com (prod) / staging.app.clothinkai.com |
| Build Args | `VITE_API_BASE_URL=https://api.clothinkai.com` |
| Health Check | TCP 80 |

### 3.2 backend
| 配置项 | 值 |
|---|---|
| Source | GitHub `clothinkai/clothing-erp`，root `/backend` |
| Build | Dockerfile |
| Port | 8000 |
| Domain | api.clothinkai.com / staging.api.clothinkai.com |
| CMD | 默认（`uvicorn app.main:app ...`） |
| Health Check | HTTP `/ready`，30s |

### 3.3 celery-worker
| 配置项 | 值 |
|---|---|
| Source | 同 backend |
| CMD | `celery -A app.core.celery_app worker --loglevel=info --concurrency=4 --queues=default,backup` |
| Domain | 无（不暴露公网） |
| Health Check | exec `celery -A app.core.celery_app inspect ping` |

### 3.4 celery-beat
| 配置项 | 值 |
|---|---|
| Source | 同 backend |
| CMD | `celery -A app.core.celery_app beat --loglevel=info --pidfile=/tmp/celerybeat.pid --schedule=/tmp/celerybeat-schedule` |
| Domain | 无 |
| Replicas | **必须 1**（多实例会重复触发定时任务） |

### 3.5 postgres / redis 插件
通过 Zeabur 控制台 1-click 添加。Zeabur 自动注入连接 URL 到环境变量。

---

## 4. 部署 checklist

### 4.1 首次部署（U01 上线）

#### 准备阶段
- [ ] 域名 `clothinkai.com` 已注册
- [ ] Cloudflare R2 4 个桶已建（按 infrastructure-design.md 5.1）
- [ ] R2 API Token 已生成（限制到 4 个桶的 R/W 权限）
- [ ] Sentry 账号 + 2 个项目（backend / frontend）
- [ ] GitHub 仓库 `clothinkai/clothing-erp` 已创建
- [ ] Zeabur 账号 + HK 区域

#### 数据库设置
- [ ] 在 Zeabur 创建 production 项目
- [ ] 添加 PostgreSQL 16 插件（1 vCPU / 1GB / 10GB）
- [ ] 添加 Redis 插件（256MB）
- [ ] 通过 Zeabur PG Web 控制台执行 `migrations/001_create_roles.sql` 创建 3 个 role
- [ ] 记录 3 个 role 的密码到密码管理器

#### 密钥准备
- [ ] 生成 JWT_SECRET：`openssl rand -hex 32`
- [ ] 生成 CREDENTIAL_MASTER_KEY 占位值：`openssl rand -base64 32`
- [ ] 在 Zeabur Secrets 添加所有必需变量

#### 部署服务
- [ ] 创建 `frontend` 服务
- [ ] 创建 `backend` 服务，绑定环境变量 + 健康检查 `/ready`
- [ ] 创建 `celery-worker` 服务，CMD 覆盖
- [ ] 创建 `celery-beat` 服务，CMD 覆盖，Replicas=1

#### 数据库迁移
- [ ] 在 GitHub Actions 手动触发 `migrate.yml` workflow，environment=production
- [ ] 等到 `alembic upgrade head` 成功（应执行：001 schema / 002 RLS / 003 seed）
- [ ] 在 PG 控制台验证：`SELECT count(*) FROM tenant` 应 = 1，`SELECT count(*) FROM role` 应 = 10

#### 部署应用
- [ ] 推 main 分支，触发自动部署
- [ ] 等部署完成（约 5-10 分钟）

#### 域名绑定
- [ ] Zeabur 给 frontend / backend 服务分别绑定域名
- [ ] 域名注册商添加 4 个 CNAME（app / api / staging.app / staging.api）
- [ ] 等 TLS 证书签发（约 5-15 分钟）

#### 启动管理员
- [ ] 查看 Zeabur backend 日志，找到首启动打印的 admin 临时密码
- [ ] 立即记录到密码管理器，**Zeabur 日志请尽快清除**
- [ ] 用 admin 登录 `app.clothinkai.com`，强制修改密码
- [ ] 在 audit_log 中验证有 `initial_admin_created` 和 `password_change` 记录

#### 健康检查
- [ ] `curl https://api.clothinkai.com/health` → 200
- [ ] `curl https://api.clothinkai.com/ready` → 200，DB + Redis ok
- [ ] Sentry 触发测试错误，验证捕获
- [ ] 等到次日 03:30 验证 backup 任务执行（查 backup_record 表）

### 4.2 staging 部署
重复 4.1 流程，项目名 `clothing-erp-staging`，域名 `staging.*`，R2 路径加 `staging/` 前缀。

### 4.3 持续部署
后续单元（U02-U18）只需推 main → 自动 redeploy；如有 schema 变更：先跑 migrate.yml workflow。

---

## 5. 本地开发 docker-compose

```yaml
version: '3.9'

services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: clothing_erp
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports: ["5432:5432"]
    volumes:
      - pg-data:/var/lib/postgresql/data
      - ./backend/alembic/init/001_create_roles.sql:/docker-entrypoint-initdb.d/001_create_roles.sql:ro

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: [redis-data:/data]

  backend:
    build: { context: ., dockerfile: backend/Dockerfile }
    env_file: backend/.env
    ports: ["8000:8000"]
    depends_on: [postgres, redis]
    volumes: [./backend:/app]
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  celery-worker:
    build: { context: ., dockerfile: backend/Dockerfile }
    env_file: backend/.env
    depends_on: [postgres, redis]
    volumes: [./backend:/app]
    command: celery -A app.core.celery_app worker --loglevel=info

  celery-beat:
    build: { context: ., dockerfile: backend/Dockerfile }
    env_file: backend/.env
    depends_on: [postgres, redis]
    volumes: [./backend:/app]
    command: celery -A app.core.celery_app beat --loglevel=info

  frontend:
    image: node:20-alpine
    working_dir: /app
    volumes: [./frontend:/app]
    ports: ["5173:5173"]
    command: sh -c "npm install && npm run dev -- --host 0.0.0.0"
    environment:
      VITE_API_BASE_URL: http://localhost:8000

volumes:
  pg-data:
  redis-data:
```

### 本地启动流程

```bash
# 1. 复制环境变量
cp .env.example backend/.env
# 编辑 backend/.env 填入 JWT_SECRET 等

# 2. 启动基础设施
docker compose up -d postgres redis

# 3. 数据库迁移
docker compose run --rm backend alembic upgrade head

# 4. 启动应用
docker compose up backend celery-worker celery-beat frontend

# 5. 查找 initial admin 密码
docker compose logs backend | grep "Initial admin"
```

---

## 6. 监控告警

### 6.1 关键告警（U01 必配）

| 事件 | 来源 | 通道 |
|---|---|---|
| backend 连续 10 次 5xx | Sentry | 邮件 |
| backup_failed_terminal | Sentry capture_exception | 邮件 |
| Zeabur 服务重启异常 | Zeabur 内置 | 邮件 |

### 6.2 V1+ 增强（U15 NFR06）
- 企微推送替代邮件
- Prometheus + Grafana 仪表盘

---

## 7. 容灾与恢复

| 场景 | 恢复手段 |
|---|---|
| Zeabur PG 实例临时宕机 | 等 Zeabur 自动恢复（通常 < 5 分钟） |
| Zeabur PG 数据损坏 | 从 R2 backups/daily/ 拉最近一份恢复（restore_backup.py） |
| Zeabur 区域故障 | 切换备用区域 + 恢复备份（RTO 4h） |
| Redis 故障 | 数据可重建，应用回退到 DB 查询；Celery 未消费任务丢失 |
| 采集 Worker 故障（U13） | 不影响 U01；U13 单独设计 |

---

## 8. 一致性校验

| 校验 | 结果 |
|---|---|
| 与 infrastructure-design.md 服务清单一致 | ✅ |
| Dockerfile 含 pg_dump 客户端 | ✅ |
| celery-beat Replicas=1（防重复触发） | ✅ |
| migrate.yml 与 Q11=B 决策一致（专用 job） | ✅ |
| 部署 checklist 覆盖首次部署所有前置 | ✅ |
| 健康检查路径与 NFR Design `/ready` 一致 | ✅ |
| 本地 docker-compose 与生产 6 服务对齐 | ✅ |
