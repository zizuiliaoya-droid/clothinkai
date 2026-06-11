"""U18 DeepSeek 客户端（httpx /chat/completions + 优雅降级）。

未配置 API_KEY / 超时 / HTTP 错误 / 非 200 / JSON 解析失败 → AiServiceUnavailableError(503)。
API key 不入日志。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.config import settings
from app.modules.ai.exceptions import AiServiceUnavailableError


def build_ai_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.DEEPSEEK_TIMEOUT)


class DeepSeekClient:
    """DeepSeek Chat Completions（OpenAI 兼容）异步客户端。"""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def chat(
        self, messages: list[dict], *, model: str | None = None
    ) -> dict[str, Any]:
        if not settings.DEEPSEEK_API_KEY:
            raise AiServiceUnavailableError()
        t0 = time.monotonic()
        try:
            resp = await self._http.post(
                f"{settings.DEEPSEEK_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model or settings.DEEPSEEK_MODEL,
                    "messages": messages,
                    "stream": False,
                },
            )
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            raise AiServiceUnavailableError() from exc
        if resp.status_code != 200:
            raise AiServiceUnavailableError()
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AiServiceUnavailableError() from exc
        return {
            "content": content,
            "model": data.get("model", model or settings.DEEPSEEK_MODEL),
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }


__all__ = ["DeepSeekClient", "build_ai_http_client"]
