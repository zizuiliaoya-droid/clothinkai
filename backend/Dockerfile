# syntax=docker/dockerfile:1.6

# 阶段 1：依赖安装层（缓存友好）
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# 阶段 2：运行时镜像
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/root/.local/bin:$PATH

# 安装 PostgreSQL 客户端（pg_dump 备份用）+ curl（健康检查）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .

EXPOSE 8000

# 默认 CMD 给 backend 服务用；celery 服务通过覆盖 CMD 复用此镜像
#
# 启动前先 alembic upgrade head：部署流程里 migration 原本要人工另外触发
# （migrate.yml 是手动 workflow），一旦漏掉，新代码配旧 schema 会直接 500。
# 放在这里让它随部署自动完成，且失败就起不来 —— fail fast 比 schema 不匹配安全。
# 只有 backend 走这个默认 CMD；celery worker/beat 覆盖了 CMD，不会重复 migrate。
# exec 让 uvicorn 接替 shell 成为主进程，否则 SIGTERM 不会转发过去，
# 容器停止只能等超时被 SIGKILL（连接来不及优雅关闭）。
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://localhost:8000/ready || exit 1
