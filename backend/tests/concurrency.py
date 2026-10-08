"""跨连接并发测试的 helper（流程线设计 9.2「并发 helper」）。

``session`` fixture 是单连接 + 外层事务回滚，测不了并发：其他连接看不到它的数据。
并发测试改用「自包含 committed 数据 + finally 清理」，这里抽出共用的三样：

- ``default_tenant_id(engine)``：003 seed 的默认租户（最早建的那个）
- ``committed(engine)``：退出时提交的独立会话，造数 / 清理用
- ``run_concurrently(engine, n, attempt)``：n 个独立会话同时跑 ``attempt(session, i)``

seed 与清理仍由各测试自己写（谁造的谁删，删的顺序跟着各自的外键走）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import tenant_id_ctx

T = TypeVar("T")


def _maker(engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def default_tenant_id(engine: Any) -> UUID:
    """003 seed 的默认租户 id；没有就直接失败（迁移没跑）。"""
    async with _maker(engine)() as s:
        row = (
            await s.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
        ).first()
    assert row is not None, "默认 tenant 缺失（003 seed 未跑）"
    tid: UUID = row[0]
    return tid


@asynccontextmanager
async def committed(engine: Any) -> AsyncIterator[AsyncSession]:
    """独立会话，块内正常结束就提交；异常时回滚并原样抛出。"""
    async with _maker(engine)() as s:
        try:
            yield s
        except BaseException:
            await s.rollback()
            raise
        await s.commit()


async def run_concurrently(
    engine: Any,
    n: int,
    attempt: Callable[[AsyncSession, int], Awaitable[T]],
    *,
    tenant_id: UUID | None = None,
) -> list[T]:
    """n 个独立会话并发跑 ``attempt(session, i)``，按 i 的顺序返回结果。

    每次 attempt 一个新会话（一条独立连接），期间 ``tenant_id_ctx`` 设成
    ``tenant_id``（不给就用默认租户），结束还原。attempt 自己决定提交与否；
    异常不吞，``asyncio.gather`` 原样抛出第一个——要统计成功 / 冲突的，
    在 attempt 里自己 catch 再返回标记。
    """
    tid = tenant_id if tenant_id is not None else await default_tenant_id(engine)
    maker = _maker(engine)

    async def _one(i: int) -> T:
        async with maker() as s:
            tok = tenant_id_ctx.set(tid)
            try:
                return await attempt(s, i)
            finally:
                tenant_id_ctx.reset(tok)

    return list(await asyncio.gather(*[_one(i) for i in range(n)]))
