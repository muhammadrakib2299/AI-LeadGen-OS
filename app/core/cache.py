"""Async key-value cache with TTL.

Two implementations:

- `InMemoryKVCache`: dict + monotonic expiry. Fine for tests and single-process
  worker setups where we don't care about surviving restarts.
- `RedisKVCache`: thin wrapper over redis-py async client. Shared across worker
  processes; what production uses.

Values are JSON-serialized so cache contents are inspectable with `redis-cli`.
Errors from the backend never bubble up — a cache that crashes the call path
is worse than no cache. Misses and backend failures both return `None`.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Protocol, runtime_checkable

import redis.asyncio as redis

from app.core.logging import get_logger

log = get_logger(__name__)


@runtime_checkable
class KVCache(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, *, ttl_s: int) -> None: ...
    async def delete(self, key: str) -> None: ...


class InMemoryKVCache:
    """Async-safe in-process cache. Not durable across restarts."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: Any, *, ttl_s: int) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic() + max(1, ttl_s), value)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)


class RedisKVCache:
    """Redis-backed cache. JSON values, per-key TTL."""

    def __init__(self, client: redis.Redis, *, namespace: str = "leadgen:cache") -> None:
        self._client = client
        self._ns = namespace

    def _k(self, key: str) -> str:
        return f"{self._ns}:{key}"

    async def get(self, key: str) -> Any | None:
        try:
            raw = await self._client.get(self._k(key))
        except redis.RedisError as exc:
            log.warning("cache_get_failed", key=key, error=str(exc))
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            log.warning("cache_decode_failed", key=key, error=str(exc))
            return None

    async def set(self, key: str, value: Any, *, ttl_s: int) -> None:
        try:
            await self._client.set(self._k(key), json.dumps(value), ex=max(1, ttl_s))
        except (redis.RedisError, TypeError, ValueError) as exc:
            log.warning("cache_set_failed", key=key, error=str(exc))

    async def delete(self, key: str) -> None:
        try:
            await self._client.delete(self._k(key))
        except redis.RedisError as exc:
            log.warning("cache_delete_failed", key=key, error=str(exc))
