"""Celery 任务的统一异步入口（新事件循环 + 引擎连接池回收）。

Celery worker 以同步方式调用任务函数，而本项目业务逻辑是 async 的，因此每个任务
都要自己开一个事件循环（``asyncio.run``）。

问题在于 SQLAlchemy 异步引擎带连接池，池里的 asyncpg 连接绑定在**创建它的那个
事件循环**上。``asyncio.run`` 结束时循环被关闭，但连接仍留在池中；下一个任务用
新循环复用这些连接就会踩到::

    RuntimeError: Task ... got Future ... attached to a different loop
    RuntimeError: Event loop is closed

表现为任务挂起或导入批次永久停在 ``processing``。同一个 worker 进程里只要有一个
任务不 dispose，就会污染连接池、连带拖垮之后所有任务（例如每 15 分钟跑一次的
``recover_stalled_crawler_imports``）。

因此**每个任务结束都必须 dispose 引擎**，让下一个任务在新循环里建新连接。

所有 Celery 任务入口一律用 :func:`run_async_task` 代替裸 ``asyncio.run``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

import sentry_sdk

log = logging.getLogger(__name__)

T = TypeVar("T")


async def _dispose_engines() -> None:
    """回收两个异步引擎的连接池。

    逐个 dispose 并各自兜异常：其一失败不能连带跳过另一个，也不能盖掉任务本身的
    返回值或异常（Celery 的 retry 判定依赖原始异常）。
    """
    from app.core.db import engine_app, engine_bypass

    for name, engine in (("app", engine_app), ("bypass", engine_bypass)):
        try:
            await engine.dispose()
        except Exception as exc:  # dispose 失败不得掩盖任务结果
            log.warning("task_engine_dispose_failed", extra={"engine": name})
            sentry_sdk.capture_exception(exc)


async def _with_engine_dispose(coro: Coroutine[Any, Any, T]) -> T:
    try:
        return await coro
    finally:
        await _dispose_engines()


def run_async_task(coro: Coroutine[Any, Any, T]) -> T:
    """在独立事件循环中执行协程，结束后 dispose 异步引擎连接池。

    Args:
        coro: 任务实现协程。**在调用处创建**（协程对象本身不绑定事件循环，
            由本函数内的 ``asyncio.run`` 驱动）。

    Returns:
        协程的返回值。协程抛出的异常原样向上传播，供 Celery 判定 retry。
    """
    return asyncio.run(_with_engine_dispose(coro))
