"""Async circuit breaker for external dependencies.

Classic three-state machine:

- **closed**: calls pass through; failures are counted.
- **open**: once consecutive failures reach the threshold, calls fail fast
  with `CircuitOpenError` until the cooldown elapses.
- **half_open**: one probe call is allowed. Success closes the circuit;
  failure reopens it for another cooldown.

Why not a sliding window over time? A small solo-operator pipeline doesn't
need it — one bad dependency run tends to produce a burst of failures and
the consecutive counter is both simpler and more predictable. Worth
revisiting if we see flappy breakers under real traffic.

Breakers are per-process (in-memory). Multi-worker fleets would share via
Redis; single-worker is fine for Phase 2.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal, TypeVar

from app.core.logging import get_logger

log = get_logger(__name__)

CircuitState = Literal["closed", "open", "half_open"]

T = TypeVar("T")


class CircuitOpenError(Exception):
    """Raised when the breaker is open and short-circuiting calls."""

    def __init__(self, name: str, cooldown_remaining_s: float) -> None:
        super().__init__(
            f"circuit '{name}' is open; retry in ~{cooldown_remaining_s:.1f}s"
        )
        self.name = name
        self.cooldown_remaining_s = cooldown_remaining_s


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        cooldown_s: float = 30.0,
        expected_exceptions: tuple[type[BaseException], ...] = (Exception,),
    ) -> None:
        self.name = name
        self._threshold = failure_threshold
        self._cooldown = cooldown_s
        self._expected = expected_exceptions
        self._state: CircuitState = "closed"
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(self, func: Callable[[], Awaitable[T]]) -> T:
        """Invoke `func` through the breaker.

        Raises `CircuitOpenError` without calling `func` when the circuit
        is open and still within its cooldown window.
        """
        await self._check_state()
        try:
            result = await func()
        except self._expected as exc:
            await self._record_failure(exc)
            raise
        else:
            await self._record_success()
            return result

    async def _check_state(self) -> None:
        async with self._lock:
            if self._state == "open":
                elapsed = time.monotonic() - (self._opened_at or 0.0)
                remaining = self._cooldown - elapsed
                if remaining > 0:
                    raise CircuitOpenError(self.name, remaining)
                # Cooldown expired — let one probe through.
                self._state = "half_open"
                log.info("circuit_half_open", name=self.name)

    async def _record_failure(self, exc: BaseException) -> None:
        async with self._lock:
            if self._state == "half_open":
                # Probe failed — reopen for another cooldown.
                self._state = "open"
                self._opened_at = time.monotonic()
                log.warning(
                    "circuit_reopened",
                    name=self.name,
                    error=type(exc).__name__,
                )
                return

            self._failures += 1
            if self._failures >= self._threshold and self._state == "closed":
                self._state = "open"
                self._opened_at = time.monotonic()
                log.warning(
                    "circuit_opened",
                    name=self.name,
                    failures=self._failures,
                    last_error=type(exc).__name__,
                )

    async def _record_success(self) -> None:
        async with self._lock:
            if self._state == "half_open":
                log.info("circuit_closed_after_probe", name=self.name)
            self._state = "closed"
            self._failures = 0
            self._opened_at = None

    async def reset(self) -> None:
        """Manual reset — for tests or admin endpoints."""
        async with self._lock:
            self._state = "closed"
            self._failures = 0
            self._opened_at = None
