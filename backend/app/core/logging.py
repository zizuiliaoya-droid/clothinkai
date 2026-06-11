"""structlog 配置 + 敏感字段 redact。

按 NFR Design 第 6 节决策：contextvars + merge_contextvars，输出 JSON。
"""

from __future__ import annotations

import logging
import sys
from typing import Any, MutableMapping

import structlog

from app.core.config import settings

# ---------------------------------------------------------------------------
# 敏感字段 redact
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "old_password",
        "new_password",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "jwt_secret",
        "credential_master_key",
        "r2_secret_access_key",
        "authorization",
    }
)


def _redact_sensitive(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """递归 redact 敏感字段。"""

    def _redact(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: ("***REDACTED***" if k.lower() in _SENSITIVE_KEYS else _redact(v))
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_redact(item) for item in obj]
        return obj

    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***REDACTED***"
        else:
            event_dict[key] = _redact(event_dict[key])
    return event_dict


# ---------------------------------------------------------------------------
# 配置入口
# ---------------------------------------------------------------------------


def configure_logging() -> None:
    """初始化 structlog + 标准库 logging。"""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # 标准库 logging 输出到 stdout（structlog 处理器接管格式化）
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # 开发环境用 ConsoleRenderer 更可读；生产用 JSONRenderer
    if settings.ENVIRONMENT == "development":
        renderer: Any = structlog.dev.ConsoleRenderer(colors=False)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_sensitive,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# 便捷别名
def get_logger(name: str | None = None) -> Any:
    return structlog.get_logger(name)
