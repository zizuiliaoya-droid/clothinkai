"""轻量级本地同事务事件总线（U04 引入，后续单元复用）。

按 NFR Design P-U04-02 设计：
- **同事务同步触发**：handler 抛异常自然冒泡到 service，整个事务回滚（强一致）
- **事件分类**（FB4）：
    * ``required_handler=True`` 的事件无 handler 时抛 ``MissingRequiredHandlerError``
    * ``required_handler=False`` 的事件无 handler 时 no-op（仅 warning + 指标）
- **subscribe 幂等**（FB6）：同一 ``(event_type, handler)`` 重复注册仅生效一次
- **clear_handlers**：测试 / 启动前清空，防热重载累计

事件类型由各业务模块自行定义（如 ``modules/promotion/events.py``），
事件实例需具备：
    - ``event_type: ClassVar[str]``（必需）
    - ``required_handler: ClassVar[bool]``（可选，默认 False）

V1+ 如需跨进程：升级到 Celery / Redis Streams。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.exceptions import MissingRequiredHandlerError

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------


EventHandler = Callable[[Any, Any], Awaitable[None]]
"""(event, session) -> None。session 为当前事务的 AsyncSession。"""


# ---------------------------------------------------------------------------
# 注册表（模块级 dict，进程单例）
# ---------------------------------------------------------------------------


_handlers: dict[str, list[EventHandler]] = {}


def subscribe(event_type: str, handler: EventHandler) -> None:
    """注册事件监听器。

    幂等保证（FB6）：
    - 同一 ``(event_type, handler)`` 重复注册仅生效一次
    - 重复注册仅 warning，不抛错（避免热重载场景启动失败）
    """
    handlers = _handlers.setdefault(event_type, [])
    if handler in handlers:
        log.warning(
            "event_handler_already_registered",
            extra={
                "event_type": event_type,
                "handler": getattr(handler, "__qualname__", str(handler)),
            },
        )
        return
    handlers.append(handler)
    log.info(
        "event_handler_subscribed",
        extra={
            "event_type": event_type,
            "handler": getattr(handler, "__qualname__", str(handler)),
        },
    )


def unsubscribe(event_type: str, handler: EventHandler) -> None:
    """主动移除监听器（主要供测试使用）。"""
    handlers = _handlers.get(event_type)
    if handlers and handler in handlers:
        handlers.remove(handler)


def clear_handlers() -> None:
    """清空所有 handlers。

    使用场景：
    - 测试 fixture teardown
    - 应用启动 ``register_event_listeners()`` 入口（防热重载累计）
    """
    _handlers.clear()


def get_handlers(event_type: str) -> list[EventHandler]:
    """返回指定 event_type 的 handler 列表副本（供测试断言）。"""
    return list(_handlers.get(event_type, []))


async def dispatch(event: Any, *, session: Any) -> None:
    """同事务同步触发事件。

    异常处理：
    - handler 抛异常自然冒泡 → service commit 前 → 事务回滚
    - 无 handler 且 ``required_handler=True``：抛 ``MissingRequiredHandlerError``
    - 无 handler 且 ``required_handler=False``：no-op + warning + 指标
    """
    # 延迟导入指标避免 core/events 循环依赖 core/metrics（metrics 未来可能依赖 events）
    from app.core.metrics import settlement_requested_events_total

    event_type = getattr(event, "event_type", None)
    if not event_type:
        raise ValueError(
            f"Event {type(event).__name__} missing event_type ClassVar"
        )

    handlers = _handlers.get(event_type, [])
    if not handlers:
        if getattr(event, "required_handler", False):
            settlement_requested_events_total.labels(result="missing_handler").inc()
            raise MissingRequiredHandlerError(
                f"Event {event_type} requires a handler but none registered. "
                "Check that downstream module is deployed.",
                details={"event_type": event_type},
            )
        # 通知类无 handler：no-op + 指标
        settlement_requested_events_total.labels(result="no_handler").inc()
        log.warning("event_no_handler", extra={"event_type": event_type})
        return

    try:
        for handler in handlers:
            await handler(event, session)
    except Exception:
        settlement_requested_events_total.labels(result="handler_failed").inc()
        raise
    settlement_requested_events_total.labels(result="dispatched").inc()


__all__ = [
    "EventHandler",
    "clear_handlers",
    "dispatch",
    "get_handlers",
    "subscribe",
    "unsubscribe",
]
