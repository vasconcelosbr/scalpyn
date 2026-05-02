"""Tests for the never-die WS reconnect loop and the order-flow window
standardization (Task #180).

Background
----------
Production hit the symptom that the Gate.io WS client died silently
after 20 consecutive failures because ``_run_with_backoff`` had a hard
``return`` once ``attempt >= RECONNECT_MAX_RETRIES``.  The Redis trade
buffer then aged out and the system entered a structural NO_DATA state
that only recovered after a deploy/restart.

In parallel, ``services/robust_indicators/compute.py`` was calling
``get_order_flow_data(symbol)`` without ``window_seconds``, falling
through to the 60-second default while the Celery indicator pipeline
used 300 s — producing flapping VALID ↔ NO_DATA values for the same
symbol depending on which pipeline ran last.

These tests pin both fixes:

  1. The backoff loop tolerates ``> RECONNECT_MAX_RETRIES`` failures in
     a row and only exits because ``self._running`` is flipped to
     False externally — never because of an internal retry cap.
  2. ``logger.critical`` is emitted exactly at every multiple of
     ``RECONNECT_MAX_RETRIES`` (20, 40, 60, …) and at no other count.
  3. After a successful connection (``coro_factory`` returns without
     raising while still running), the attempt counter and backoff
     delay are reset, so the next reconnect starts from
     ``RECONNECT_BASE_DELAY`` rather than the post-failure backoff.
  4. ``robust_indicators.compute.compute_robust_indicators`` calls
     ``get_order_flow_data`` with ``window_seconds=300``, matching the
     Celery pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.websocket import gate_ws_client as ws_mod
from app.websocket.gate_ws_client import (
    GateWSClient,
    RECONNECT_BASE_DELAY,
    RECONNECT_MAX_RETRIES,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_client() -> GateWSClient:
    """A GateWSClient that won't accidentally try to open real sockets.

    ``_run_with_backoff`` only reads ``self._running``; we never call
    ``start()`` so the contracts/spot_pairs lists can be empty.
    """
    client = GateWSClient(
        api_key="",
        api_secret="",
        contracts=[],
        spot_pairs=["BTC_USDT"],
    )
    client._running = True
    return client


# ---------------------------------------------------------------------------
# (1) never-die: tolerates > MAX_RETRIES failures without returning
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_backoff_loop_tolerates_more_than_max_retries(caplog):
    """Pre-fix this returned at ``attempt >= 20``.  Post-fix it must keep
    going indefinitely; only an external ``stop()`` (``_running = False``)
    breaks the loop."""
    client = _make_client()

    target_attempts = RECONNECT_MAX_RETRIES * 2 + 5  # 45 by default
    counter = {"n": 0}

    async def always_fail() -> None:
        counter["n"] += 1
        if counter["n"] >= target_attempts:
            # Simulate stop() being called from the outside so the loop
            # exits via the normal shutdown path; otherwise we'd run
            # forever (which is exactly the bug we want to lock in).
            client._running = False
        raise RuntimeError("simulated network drop")

    # Patch asyncio.sleep so the test runs in milliseconds rather than
    # `sum(min(2**i, 60) for i in range(45))` real seconds.
    with patch.object(ws_mod.asyncio, "sleep", new=AsyncMock(return_value=None)):
        with caplog.at_level(logging.WARNING, logger=ws_mod.logger.name):
            await client._run_with_backoff("spot", always_fail)

    assert counter["n"] >= target_attempts, (
        f"loop must have attempted at least {target_attempts} reconnects; "
        f"got {counter['n']}"
    )

    # The loop must NOT have logged "giving up" — that line was the bug.
    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "giving up" not in rendered.lower(), (
        f"loop must never give up — found 'giving up' in logs:\n{rendered}"
    )


# ---------------------------------------------------------------------------
# (2) CRITICAL log fires exactly at multiples of RECONNECT_MAX_RETRIES
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_critical_log_fires_at_each_multiple_of_max_retries(caplog):
    """A page every ``MAX_RETRIES`` failures keeps a real signal in
    Sentry without storming it on every individual retry."""
    client = _make_client()

    # Aim for exactly 3 cycles -> CRITICAL at 20, 40, 60.
    target_attempts = RECONNECT_MAX_RETRIES * 3
    counter = {"n": 0}

    async def always_fail() -> None:
        counter["n"] += 1
        if counter["n"] >= target_attempts:
            client._running = False
        raise RuntimeError("boom")

    with patch.object(ws_mod.asyncio, "sleep", new=AsyncMock(return_value=None)):
        with caplog.at_level(logging.WARNING, logger=ws_mod.logger.name):
            await client._run_with_backoff("spot", always_fail)

    crit_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(crit_records) == 3, (
        f"expected 3 CRITICAL logs (at attempts 20, 40, 60); "
        f"got {len(crit_records)}: {[r.getMessage() for r in crit_records]}"
    )

    # Each CRITICAL must mention "STILL RETRYING" so the on-call
    # immediately understands this is not a final-give-up.
    for r in crit_records:
        assert "STILL RETRYING" in r.getMessage(), (
            f"CRITICAL log must contain 'STILL RETRYING'; got: {r.getMessage()}"
        )

    # Every other failure between multiples is at WARN level (or below);
    # we should have exactly target_attempts - 3 WARN logs from the loop.
    # (filter by the dedicated 'WS lost' marker to avoid catching the
    # 'WS exited cleanly' branch from other tests' shared logger.)
    warn_lost = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "WS lost (attempt" in r.getMessage()
    ]
    assert len(warn_lost) == target_attempts - 3


# ---------------------------------------------------------------------------
# (3) successful connection resets attempt counter + backoff delay
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_clean_exit_resets_attempt_counter_and_delay(caplog):
    """If the WS connects, runs, and returns cleanly while ``_running``
    is still True, the next failure should restart the backoff at
    ``RECONNECT_BASE_DELAY`` rather than continuing the post-failure
    exponential growth."""
    client = _make_client()

    # Sequence: 5 failures (delay grows: 1, 2, 4, 8, 16) → clean exit
    # (must reset to delay=1) → 1 more failure (next sleep should be
    # delay=1, NOT 32).
    script: List[str] = ["fail"] * 5 + ["clean"] + ["fail"] + ["stop"]
    pos = {"i": 0}

    async def coro() -> None:
        i = pos["i"]
        pos["i"] += 1
        if i >= len(script):
            client._running = False
            return
        action = script[i]
        if action == "fail":
            raise RuntimeError("transient drop")
        if action == "clean":
            return  # clean return while _running stays True
        if action == "stop":
            client._running = False
            return

    sleep_delays: List[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_delays.append(delay)

    with patch.object(ws_mod.asyncio, "sleep", new=fake_sleep):
        await client._run_with_backoff("spot", coro)

    # The loop layout is: (try → on-fail-log → sleep(delay) → delay*=2),
    # so each iteration's recorded sleep is the *current* delay BEFORE
    # the doubling at the end of that iteration.
    #
    # Without reset, 5 consecutive failures grow the delay 1→2→4→8→16,
    # the next sleep would be 32, then 60 (capped). With the clean-exit
    # reset in place we expect:
    #   after fail #1   → sleep 1.0,  delay→2
    #   after fail #2   → sleep 2.0,  delay→4
    #   after fail #3   → sleep 4.0,  delay→8
    #   after fail #4   → sleep 8.0,  delay→16
    #   after fail #5   → sleep 16.0, delay→32
    #   after clean     → reset(delay=1.0), sleep 1.0, delay→2
    #   after fail #6   → sleep 2.0  ← the regression-proof: WITHOUT
    #                                    the reset this would be 32.0
    #   after stop      → loop breaks before sleeping
    assert sleep_delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0], (
        f"pre-clean exponential backoff broken; got {sleep_delays[:5]}"
    )
    # The clean-exit branch resets, so the sleep emitted by that
    # iteration is the reset base.
    assert sleep_delays[5] == RECONNECT_BASE_DELAY, (
        f"clean exit must reset backoff to {RECONNECT_BASE_DELAY}s; "
        f"got {sleep_delays[5]}"
    )
    # The next failure's sleep must reflect the post-reset growth
    # (1 → 2), NOT the pre-reset growth that would have been
    # min(16*2, 60) = 32. That delta is the regression we're locking in.
    assert sleep_delays[6] == RECONNECT_BASE_DELAY * 2, (
        f"first failure after reset must come from the reset path "
        f"(expected {RECONNECT_BASE_DELAY * 2}s, post-reset doubling); "
        f"got {sleep_delays[6]}s — looks like the reset did not take effect"
    )
    assert sleep_delays[6] != 32.0, (
        "post-reset sleep equals 32s, which is exactly the value the "
        "PRE-fix loop would have produced (continuing 1→2→4→8→16→32 "
        "without a reset). Reset did NOT happen."
    )

    # The clean-exit log line must mention "still running — reconnecting"
    # so the on-call knows it wasn't a stop().
    msgs = [r.getMessage() for r in caplog.records]
    assert any("still running" in m for m in msgs), (
        f"clean-exit branch must log 'still running'; got:\n{msgs}"
    )


# ---------------------------------------------------------------------------
# (4) robust_indicators uses window_seconds=300
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_robust_indicators_calls_order_flow_with_300s_window(monkeypatch):
    """Pre-fix this defaulted to 60 s, causing VALID/NO_DATA flapping.
    Post-fix it must explicitly request the same 300 s window the Celery
    pipeline uses, aligned with the Redis trade buffer TTL (Task #171)."""
    from app.services.robust_indicators import compute as compute_mod

    captured = {}

    async def fake_get_order_flow_data(symbol, *, window_seconds, **_):
        captured["symbol"] = symbol
        captured["window_seconds"] = window_seconds
        return {
            "taker_buy_volume":  1.0,
            "taker_sell_volume": 1.0,
            "taker_ratio":       0.5,
            "buy_pressure":      0.5,
            "volume_delta":      0.0,
            "taker_source":      "gate_trades_ws",
            "taker_window":      f"{window_seconds}s",
        }

    # All three are lazy imports inside ``compute_indicators_robust`` —
    # patch them on their source modules so the in-function ``from …
    # import …`` resolves to our fakes.
    class _FakeFE:
        def __init__(self, _cfg) -> None: ...
        def calculate(self, _df, market_data=None) -> dict:  # noqa: ARG002
            return {"rsi": 55.0}

    class _FakeMDS:
        async def get_ohlcv_dataframe(self, *_a, **_kw):
            # Returning None makes ``compute_indicators_robust`` short-
            # circuit *after* the order-flow fetch — exactly what we want
            # because the assertion is about the call kwargs, not about
            # the envelope output.
            return None

    monkeypatch.setattr(
        "app.services.feature_engine.FeatureEngine", _FakeFE, raising=True,
    )
    monkeypatch.setattr(
        "app.services.market_data_service.MarketDataService", _FakeMDS, raising=True,
    )
    monkeypatch.setattr(
        "app.services.order_flow_service.get_order_flow_data",
        fake_get_order_flow_data,
        raising=True,
    )

    await compute_mod.compute_indicators_robust(
        symbol="BTC_USDT",
        timeframe="1h",
    )

    assert captured.get("window_seconds") == 300, (
        f"robust_indicators must request 300s window to match Celery "
        f"pipeline; got {captured.get('window_seconds')!r}"
    )
    assert captured.get("symbol") == "BTC_USDT"
