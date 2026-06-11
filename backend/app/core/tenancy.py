"""多租户上下文管理（contextvars + system_context）。

核心机制（详见 nfr-design/nfr-design-patterns.md 第 1, 2 节）：
- 中间件解析 JWT 后写入 contextvars
- core/db.py 的 get_session() 依赖读取 contextvars，选择 engine_app/engine_bypass，并执行 SET LOCAL
- system_context() 提供给 Celery 任务等系统场景使用
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator
from uuid import UUID

# ---------------------------------------------------------------------------
# Context Variables
# ---------------------------------------------------------------------------

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")
tenant_id_ctx: ContextVar[UUID | None] = ContextVar("tenant_id", default=None)
user_id_ctx: ContextVar[UUID | None] = ContextVar("user_id", default=None)
actor_type_ctx: ContextVar[str] = ContextVar("actor_type", default="anonymous")
bypass_rls_ctx: ContextVar[bool] = ContextVar("bypass_rls", default=False)


def get_current_tenant_id() -> UUID | None:
    return tenant_id_ctx.get()


def get_current_user_id() -> UUID | None:
    return user_id_ctx.get()


def is_bypass_rls() -> bool:
    return bypass_rls_ctx.get()


# ---------------------------------------------------------------------------
# system_context: 系统任务（Celery）显式跨租户标记
# ---------------------------------------------------------------------------


@asynccontextmanager
async def system_context() -> AsyncIterator[None]:
    """系统任务上下文管理器（绕过 RLS，仍写 audit_log）。

    示例：
        async with system_context():
            # 跨租户查询允许，core/db.py.get_session() 会选 engine_bypass
            ...
    """
    bypass_token = bypass_rls_ctx.set(True)
    actor_token = actor_type_ctx.set("system")
    try:
        yield
    finally:
        bypass_rls_ctx.reset(bypass_token)
        actor_type_ctx.reset(actor_token)
