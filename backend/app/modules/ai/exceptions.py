"""U18 AI 模块业务异常。"""

from __future__ import annotations

from app.core.exceptions import AppException


class AiServiceUnavailableError(AppException):
    """AI 服务不可用（未配置 / 超时 / 限流 / 非 200 / 解析失败）→ 优雅降级。"""

    code = "AI_SERVICE_UNAVAILABLE"
    status_code = 503
    message = "AI 服务暂时不可用，请稍后重试"


class AiDataInsufficientError(AppException):
    """历史数据不足，无法生成建议（不调 AI）。"""

    code = "AI_DATA_INSUFFICIENT"
    status_code = 422
    message = "历史数据不足，无法生成建议"


__all__ = ["AiDataInsufficientError", "AiServiceUnavailableError"]
