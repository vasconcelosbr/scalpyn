from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


def _is_transient_db_error(exc: Exception) -> bool:
    orig = getattr(exc, "orig", exc)
    name = type(orig).__name__.lower()
    message = str(orig).lower()
    return any(
        marker in name or marker in message
        for marker in (
            "deadlock",
            "serialization",
            "timeout",
            "timedout",
            "locknotavailable",
        )
    )


class UnitOfWork:
    def __init__(
        self,
        session_factory,
        state,
        *,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.2,
    ) -> None:
        self._session_factory = session_factory
        self._state = state
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds

    async def execute(self, fn: Callable[[object], Awaitable[None]], *, domain: str, key: str) -> None:
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            acquire_started = time.perf_counter()
            async with self._session_factory() as session:
                acquire_ms = (time.perf_counter() - acquire_started) * 1000
                tx_started = time.perf_counter()
                self._state.observe_acquire_latency(acquire_ms)
                try:
                    await fn(session)
                    await session.commit()
                    self._state.observe_transaction_time((time.perf_counter() - tx_started) * 1000)
                    self._state.mark_success(domain)
                    return
                except Exception as exc:
                    last_exc = exc
                    self._state.increment_rollbacks()
                    try:
                        await session.rollback()
                    except Exception as rollback_exc:
                        logger.warning("[PERSIST] rollback failed for %s: %s", key, rollback_exc)

                    if attempt < self._max_retries and _is_transient_db_error(exc):
                        self._state.increment_retries()
                        await asyncio.sleep(self._retry_delay_seconds * (attempt + 1))
                        continue

                    self._state.mark_failure(domain, key, exc)
                    raise

        if last_exc is not None:
            raise last_exc
