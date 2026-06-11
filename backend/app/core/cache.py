"""Redis 异步客户端封装。

按 NFR Design + Infrastructure Design 决策：
- 4 个 Redis DB 分片：cache(0) / celery broker(1) / celery backend(2) / 黑名单 由各自 client 处理
- 统一封装 incr / set_with_ttl / get / exists / delete
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis, from_url

from app.core.config import settings


# ---------------------------------------------------------------------------
# 单例 Redis 客户端（默认 db=0 / cache）
# ---------------------------------------------------------------------------

_redis_client: Redis | None = None


def get_redis() -> Redis:
    """返回应用缓存 Redis 客户端单例。"""
    global _redis_client
    if _redis_client is None:
        _redis_client = from_url(
            settings.REDIS_URL_CACHE,
            decode_responses=True,
            max_connections=20,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
    return _redis_client


async def close_redis() -> None:
    """优雅关闭 Redis 连接（lifespan 退出时调用）。"""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


# ---------------------------------------------------------------------------
# CacheClient 封装（高级接口）
# ---------------------------------------------------------------------------


class CacheClient:
    """缓存客户端高级封装。"""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis or get_redis()

    async def get(self, key: str) -> str | None:
        return await self._redis.get(key)

    async def set_with_ttl(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._redis.set(key, value, ex=ttl_seconds)

    async def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        await self._redis.setex(key, ttl_seconds, value)

    async def incr(self, key: str) -> int:
        """原子自增；首次创建键时调用方需要后续 expire 设置 TTL。"""
        return int(await self._redis.incr(key))

    async def expire(self, key: str, ttl_seconds: int) -> bool:
        return bool(await self._redis.expire(key, ttl_seconds))

    async def ttl(self, key: str) -> int:
        """剩余 TTL（秒）；-1 表示无过期，-2 表示键不存在。"""
        return int(await self._redis.ttl(key))

    async def exists(self, key: str) -> bool:
        return bool(await self._redis.exists(key))

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        return int(await self._redis.delete(*keys))

    async def ping(self) -> bool:
        try:
            return bool(await self._redis.ping())
        except Exception:  # noqa: BLE001
            return False


# 便捷单例（导入即可用）
cache: CacheClient = CacheClient()


# ---------------------------------------------------------------------------
# 健康检查辅助
# ---------------------------------------------------------------------------


async def check_redis_health() -> bool:
    return await cache.ping()
