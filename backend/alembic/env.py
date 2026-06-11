"""Alembic 环境配置（异步 SQLAlchemy）。

本文件由 backend/app/core/db.py 的 Base 元数据驱动 migration。
数据库 URL 从环境变量 DATABASE_URL_BYPASS 读取（用 bypass 角色避免被 RLS 拦截）。
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 引入应用 Base 元数据
from app.core.db import Base  # noqa: E402
# 引入所有模块的 models 以便 Base.metadata 收集
from app.modules.auth import models as auth_models  # noqa: F401, E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 从环境变量读取真实 DB URL（bypass 角色，绕过 RLS）
db_url = os.getenv("DATABASE_URL_BYPASS")
if not db_url:
    raise RuntimeError(
        "Alembic 需要环境变量 DATABASE_URL_BYPASS（asyncpg 驱动 + clothing_bypass 角色）"
    )
config.set_main_option("sqlalchemy.url", db_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """生成 SQL 文本（不连库）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # PostgreSQL：服务器侧默认值的比较与字段类型变更
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
