"""缓存层 — 支持 Redis（生产）和内存（开发）双模式。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .config import settings

logger = logging.getLogger(__name__)

# 全局缓存实例
_cache = None


class MemoryCache:
    """内存缓存 — 开发/测试环境使用。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, float]] = {}

    async def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at and time.time() > expires_at:
            del self._store[key]
            return None
        return value

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        expires_at = time.time() + ttl if ttl > 0 else 0
        self._store[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear_prefix(self, prefix: str) -> int:
        keys_to_delete = [k for k in self._store if k.startswith(prefix)]
        for k in keys_to_delete:
            del self._store[k]
        return len(keys_to_delete)

    async def close(self) -> None:
        pass


class RedisCache:
    """Redis 缓存 — 生产环境使用。"""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def get(self, key: str) -> Any | None:
        try:
            data = await self._redis.get(key)
            if data is None:
                return None
            return json.loads(data)
        except Exception as e:
            logger.warning("Redis GET 失败 key=%s: %s", key, e)
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        try:
            data = json.dumps(value, ensure_ascii=False, default=str)
            if ttl > 0:
                await self._redis.setex(key, ttl, data)
            else:
                await self._redis.set(key, data)
        except Exception as e:
            logger.warning("Redis SET 失败 key=%s: %s", key, e)

    async def delete(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning("Redis DELETE 失败 key=%s: %s", key, e)

    async def clear_prefix(self, prefix: str) -> int:
        try:
            keys = []
            async for key in self._redis.scan_iter(f"{prefix}*"):
                keys.append(key)
            if keys:
                return await self._redis.delete(*keys)
            return 0
        except Exception as e:
            logger.warning("Redis CLEAR 失败 prefix=%s: %s", prefix, e)
            return 0

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()


def get_cache():
    """获取缓存实例（懒加载）。"""
    global _cache
    if _cache is not None:
        return _cache

    # 尝试连接 Redis
    try:
        import redis.asyncio as aioredis
        redis_client = aioredis.from_url(
            settings.CELERY_BROKER_URL,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        _cache = RedisCache(redis_client)
        logger.info("Redis 缓存已初始化")
    except Exception as e:
        logger.info("Redis 不可用，使用内存缓存: %s", e)
        _cache = MemoryCache()

    return _cache
