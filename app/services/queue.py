"""Thin wrapper around arq so the rest of the app doesn't touch arq internals."""

from __future__ import annotations

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


_pool: ArqRedis | None = None


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_redis_pool() -> ArqRedis:
    """Lazy singleton pool — created on first enqueue."""
    global _pool
    if _pool is None:
        _pool = await create_pool(redis_settings())
    return _pool


async def close_redis_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close(close_connection_pool=True)
        _pool = None
