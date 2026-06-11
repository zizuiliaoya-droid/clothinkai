# 服装电商运营管理系统（Clothing ERP）

一站式服装电商运营管理平台。整合千牛、万相台、灰豚等多源数据，提供智能录入、可视化看板、企微自动化推送。

## 技术栈

- **前端**：React 18 + TypeScript + Ant Design 5 + Vite + React Query + Zustand
- **后端**：Python FastAPI + SQLAlchemy 2.0 (async) + asyncpg
- **数据库**：PostgreSQL 16（含 Row Level Security）
- **缓存**：Redis 7
- **任务队列**：Celery + Celery Beat
- **文件存储**：Cloudflare R2（公私桶分离）
- **部署**：Zeabur（HK 区域）+ Docker
- **监控**：Sentry + Prometheus + structlog

## 仓库结构

```
clothing-erp/
├── backend/         # FastAPI 应用 + Celery worker/beat
├── frontend/        # React SPA
├── rpa-worker/      # 外部数据采集 Worker（U13 启用）
├── docs/            # 部署与运维文档
├── docker-compose.yml
├── .env.example
└── .github/workflows/
```

## 快速开始（本地开发）

### 前置要求
- Docker + Docker Compose
- Python 3.12（如本地跑测试）
- Node.js 20（如本地跑前端）

### 启动步骤

```bash
# 1. 复制环境变量
cp .env.example backend/.env
# 编辑 backend/.env，至少需要填入 JWT_SECRET（可用 openssl rand -hex 32 生成）

# 2. 启动基础设施
docker compose up -d postgres redis

# 3. 数据库迁移
docker compose run --rm backend alembic upgrade head

# 4. 启动应用
docker compose up backend celery-worker celery-beat frontend

# 5. 查找 initial admin 临时密码（首次启动）
docker compose logs backend | grep "Initial admin"
```

访问：
- 前端：http://localhost:5173
- API 文档：http://localhost:8000/api/docs
- API 健康检查：http://localhost:8000/health

## 部署

详见 `docs/ZEABUR_SETUP.md` 和 `docs/SECRETS_SETUP.md`。

## 项目文档

- 需求规格：`aidlc-docs/inception/requirements/requirements.md`
- 用户故事：`aidlc-docs/inception/user-stories/stories.md`
- 应用设计：`aidlc-docs/inception/application-design/`
- 工作单元：`aidlc-docs/inception/application-design/unit-of-work.md`
- 详细构造：`aidlc-docs/construction/`

## 许可

私有项目。
