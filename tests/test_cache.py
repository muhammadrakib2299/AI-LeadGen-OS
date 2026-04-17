"""Tests for the KVCache primitive and its use in AnthropicClient."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as redis

from app.core.cache import InMemoryKVCache, RedisKVCache
from app.services.llm import AnthropicClient


async def test_in_memory_cache_set_and_get_roundtrips() -> None:
    cache = InMemoryKVCache()
    await cache.set("foo", {"value": 1}, ttl_s=5)
    assert await cache.get("foo") == {"value": 1}


async def test_in_memory_cache_miss_returns_none() -> None:
    cache = InMemoryKVCache()
    assert await cache.get("missing") is None


async def test_in_memory_cache_ttl_expires() -> None:
    cache = InMemoryKVCache()
    await cache.set("short", "v", ttl_s=1)
    # Force expiry by mutating stored timestamp — faster than sleeping.
    cache._store["short"] = (0.0, "v")
    assert await cache.get("short") is None


async def test_in_memory_cache_delete() -> None:
    cache = InMemoryKVCache()
    await cache.set("k", "v", ttl_s=10)
    await cache.delete("k")
    assert await cache.get("k") is None


async def test_in_memory_cache_is_async_safe_under_concurrent_access() -> None:
    cache = InMemoryKVCache()

    async def _writer(i: int) -> None:
        await cache.set(f"k{i}", i, ttl_s=5)

    await asyncio.gather(*[_writer(i) for i in range(50)])
    for i in range(50):
        assert await cache.get(f"k{i}") == i


async def test_redis_cache_get_swallows_redis_errors() -> None:
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(side_effect=redis.RedisError("nope"))
    cache = RedisKVCache(fake_client)
    assert await cache.get("anything") is None  # does not raise


async def test_redis_cache_set_swallows_redis_errors() -> None:
    fake_client = AsyncMock()
    fake_client.set = AsyncMock(side_effect=redis.RedisError("nope"))
    cache = RedisKVCache(fake_client)
    await cache.set("k", {"v": 1}, ttl_s=10)  # does not raise


async def test_redis_cache_get_returns_none_on_malformed_json() -> None:
    fake_client = AsyncMock()
    fake_client.get = AsyncMock(return_value=b"{not-json")
    cache = RedisKVCache(fake_client)
    assert await cache.get("k") is None


async def test_anthropic_client_uses_cache_on_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = InMemoryKVCache()
    # Pre-populate cache with a response for the expected key.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    client = AnthropicClient(api_key="test-key", cache=cache)
    await client._cache.set(  # type: ignore[union-attr]
        _expected_key("claude-haiku-4-5-20251001", "sys", "usr"),
        {"cached": True},
        ttl_s=60,
    )

    messages_mock = AsyncMock()
    with patch.object(client._client, "messages", messages_mock):
        result = await client.complete_json("sys", "usr")
    assert result == {"cached": True}
    messages_mock.create.assert_not_called()


async def test_anthropic_client_stores_after_miss() -> None:
    cache = InMemoryKVCache()
    client = AnthropicClient(api_key="test-key", cache=cache)

    class _Block:
        type = "text"
        text = '{"entity_type": "restaurant", "confidence": 0.9}'

    fake_resp = type("R", (), {"content": [_Block()]})()
    client._client.messages.create = AsyncMock(return_value=fake_resp)  # type: ignore[attr-defined]

    first = await client.complete_json("sys", "usr")
    assert first == {"entity_type": "restaurant", "confidence": 0.9}

    # Second call should hit cache — underlying create is only invoked once.
    second = await client.complete_json("sys", "usr")
    assert second == first
    assert client._client.messages.create.call_count == 1  # type: ignore[attr-defined]


async def test_anthropic_client_without_cache_always_calls_api() -> None:
    client = AnthropicClient(api_key="test-key")

    class _Block:
        type = "text"
        text = '{"x": 1}'

    fake_resp = type("R", (), {"content": [_Block()]})()
    client._client.messages.create = AsyncMock(return_value=fake_resp)  # type: ignore[attr-defined]

    await client.complete_json("sys", "usr")
    await client.complete_json("sys", "usr")
    assert client._client.messages.create.call_count == 2  # type: ignore[attr-defined]


def _expected_key(model: str, system: str, user: str) -> str:
    # Mirrors the private _cache_key helper — asserting the shape stays stable.
    from app.services.llm import _cache_key

    return _cache_key(model, system, user)


# Silence unused-import warnings when redis isn't reachable.
_ = Any
