"""Tests for the Gate Spot WS trade-buffer ingestion path (Task #171).

Covers:
  * ``handle_spot_trades`` writes ZADD + ZREMRANGEBYSCORE +
    ZREMRANGEBYRANK + EXPIRE into the right Redis key, including the
    non-normalised-symbol case (``BTCUSDT`` → ``trades_buffer:spot:BTC_USDT``).
  * ``order_flow_service.get_order_flow_data`` reads from the buffer
    first and returns ``taker_source = "gate_trades_ws_spot"`` when ≥1 trade
    exists in the window.
  * The same call falls back to the REST stub (``taker_source =
    "gate_io_trades"``) when the buffer is empty.

All tests use ``fakeredis.aioredis`` so they run in-process with no
network dependency.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Dict, List

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fakeredis.aioredis  # type: ignore[import-untyped]

from app.services import redis_client
from app.services.order_flow_service import get_order_flow_data
from app.websocket.event_handlers import (
    TRADE_BUFFER_TTL_SECONDS,
    _trades_buffer_key,
    handle_spot_trades,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def fake_redis():
    """Inject a fakeredis async client into the singleton for the duration of one test."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    redis_client.set_async_redis(client)
    try:
        yield client
    finally:
        await redis_client.reset_async_redis()


def _trade(*, currency_pair: str, side: str, amount: str,
           ts_ms: float, trade_id: int | None = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "currency_pair": currency_pair,
        "side": side,
        "amount": amount,
        "create_time_ms": ts_ms,
    }
    if trade_id is not None:
        out["id"] = trade_id
    return out


# ── handle_spot_trades ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_handle_spot_trades_writes_zadd_with_normalized_symbol(fake_redis):
    """``BTCUSDT`` (non-normalised) must collide with the ``BTC_USDT`` key."""
    now_ms = time.time() * 1000.0
    await handle_spot_trades([
        _trade(currency_pair="BTCUSDT", side="buy",  amount="1.0", ts_ms=now_ms - 100, trade_id=1),
        _trade(currency_pair="BTC_USDT", side="sell", amount="2.0", ts_ms=now_ms - 50,  trade_id=2),
    ])

    expected_key = _trades_buffer_key("BTCUSDT")
    assert expected_key == "trades_buffer:spot:BTC_USDT"

    # Both trades land in the *same* sorted set under the normalised key.
    n = await fake_redis.zcard(expected_key)
    assert n == 2

    # TTL was set ≥ TRADE_BUFFER_TTL_SECONDS - 1 (some Redis impls round down by 1).
    ttl = await fake_redis.ttl(expected_key)
    assert ttl > 0
    assert ttl <= TRADE_BUFFER_TTL_SECONDS

    # Members carry both side+amount (so the buffer-reader can aggregate).
    members = await fake_redis.zrange(expected_key, 0, -1)
    parsed = []
    for raw in members:
        text = raw.decode("utf-8")
        cut = text.rfind("|")
        parsed.append(json.loads(text[:cut] if cut > 0 else text))
    sides = sorted(p["s"] for p in parsed)
    assert sides == ["buy", "sell"]


@pytest.mark.anyio
async def test_handle_spot_trades_caps_per_symbol_via_zremrangebyrank(fake_redis, monkeypatch):
    """Once cap is exceeded, only the newest ``cap`` entries survive."""
    monkeypatch.setenv("TRADES_BUFFER_MAX_PER_SYMBOL", "100")
    now_ms = time.time() * 1000.0
    # Send 150 trades spaced 10 ms apart — cap=100 must drop the oldest 50.
    batch = [
        _trade(currency_pair="BTC_USDT", side="buy", amount="0.1",
               ts_ms=now_ms - (150 - i) * 10, trade_id=i)
        for i in range(150)
    ]
    await handle_spot_trades(batch)

    key = _trades_buffer_key("BTC_USDT")
    n = await fake_redis.zcard(key)
    # ``max(int(env), 100)`` so the cap is exactly 100.
    assert n == 100


@pytest.mark.anyio
async def test_handle_spot_trades_drops_aged_entries_via_zremrangebyscore(fake_redis):
    """Entries older than ``TRADE_BUFFER_TTL_SECONDS`` are pruned by score."""
    now_ms = time.time() * 1000.0
    too_old_ms = now_ms - (TRADE_BUFFER_TTL_SECONDS + 60) * 1000.0
    fresh_ms = now_ms - 1_000.0

    await handle_spot_trades([
        _trade(currency_pair="BTC_USDT", side="buy", amount="1.0", ts_ms=too_old_ms, trade_id=1),
        _trade(currency_pair="BTC_USDT", side="buy", amount="1.0", ts_ms=fresh_ms,   trade_id=2),
    ])

    key = _trades_buffer_key("BTC_USDT")
    # Only the fresh trade survives the ZREMRANGEBYSCORE cutoff.
    assert await fake_redis.zcard(key) == 1


@pytest.mark.anyio
async def test_handle_spot_trades_handles_empty_input(fake_redis):
    """Empty result is a no-op (no key created)."""
    await handle_spot_trades([])
    keys = await fake_redis.keys(b"trades_buffer:*")
    assert keys == []


@pytest.mark.anyio
async def test_handle_spot_trades_skips_malformed_entries(fake_redis):
    """A bad trade in a batch must not poison the rest of the batch."""
    now_ms = time.time() * 1000.0
    await handle_spot_trades([
        # Missing currency_pair → skipped
        {"side": "buy", "amount": "1.0", "create_time_ms": now_ms},
        # Bad side → skipped
        _trade(currency_pair="BTC_USDT", side="banana", amount="1.0", ts_ms=now_ms),
        # Good
        _trade(currency_pair="BTC_USDT", side="buy", amount="1.0", ts_ms=now_ms, trade_id=99),
    ])
    n = await fake_redis.zcard(_trades_buffer_key("BTC_USDT"))
    assert n == 1


# ── get_order_flow_data buffer-first ──────────────────────────────────────────

@pytest.mark.anyio
async def test_get_order_flow_data_reads_from_buffer_when_present(fake_redis):
    """When ≥1 buffered trade is in window → source = "gate_trades_ws_spot"."""
    now_ms = time.time() * 1000.0
    await handle_spot_trades([
        _trade(currency_pair="BTC_USDT", side="buy",  amount="3.0", ts_ms=now_ms - 1_000, trade_id=1),
        _trade(currency_pair="BTC_USDT", side="sell", amount="1.0", ts_ms=now_ms -   500, trade_id=2),
    ])

    payload = await get_order_flow_data("BTC_USDT", window_seconds=60)

    assert payload["taker_source"] == "gate_trades_ws_spot"
    assert payload["taker_buy_volume"]  == pytest.approx(3.0)
    assert payload["taker_sell_volume"] == pytest.approx(1.0)
    assert payload["taker_ratio"]       == pytest.approx(3.0 / 4.0)
    assert payload["buy_pressure"]      == pytest.approx(3.0 / 4.0)
    assert payload["volume_delta"]      == pytest.approx(2.0)


@pytest.mark.anyio
async def test_get_order_flow_data_uses_normalized_key_for_lookup(fake_redis):
    """Reader normalises ``BTCUSDT`` to the same key the handler wrote under."""
    now_ms = time.time() * 1000.0
    # Handler receives the non-normalised form
    await handle_spot_trades([
        _trade(currency_pair="BTCUSDT", side="buy", amount="2.0", ts_ms=now_ms - 100, trade_id=1),
    ])
    # Reader receives the normalised form — must still hit the same key.
    payload = await get_order_flow_data("BTC_USDT", window_seconds=60)
    assert payload["taker_source"] == "gate_trades_ws_spot"
    assert payload["taker_buy_volume"] == pytest.approx(2.0)


@pytest.mark.anyio
async def test_get_order_flow_data_falls_back_to_rest_when_buffer_empty(monkeypatch, fake_redis):
    """Empty buffer → REST stub path runs, source = "gate_io_trades"."""
    now_ms = time.time() * 1000.0

    class _FakeAdapter:
        SPOT_BASE = "https://example.test"

        @staticmethod
        def _normalize_symbol(symbol: str) -> str:
            if "_" not in symbol and symbol.endswith("USDT"):
                return symbol[:-4] + "_USDT"
            return symbol

        @staticmethod
        async def _public_get(url: str, params: Dict[str, Any]) -> List[Dict[str, Any]]:
            return [
                {"side": "buy",  "amount": "5.0",  "create_time_ms": now_ms - 2_000},
                {"side": "sell", "amount": "10.0", "create_time_ms": now_ms - 1_000},
            ]

    monkeypatch.setattr(
        "app.exchange_adapters.gate_adapter.GateAdapter",
        _FakeAdapter,
        raising=False,
    )

    payload = await get_order_flow_data("ETH_USDT", window_seconds=60)
    assert payload["taker_source"] == "gate_io_trades"
    assert payload["taker_ratio"] == pytest.approx(5.0 / 15.0)


@pytest.mark.anyio
async def test_get_order_flow_data_buffer_window_excludes_stale_trades(fake_redis):
    """Trades older than ``window_seconds`` must not contribute to the aggregate."""
    now_ms = time.time() * 1000.0
    # Inside the buffer TTL but outside a 60s read window.
    await handle_spot_trades([
        _trade(currency_pair="BTC_USDT", side="buy", amount="999.0",
               ts_ms=now_ms - 200_000, trade_id=1),
        _trade(currency_pair="BTC_USDT", side="buy", amount="1.0",
               ts_ms=now_ms - 1_000,    trade_id=2),
    ])
    payload = await get_order_flow_data("BTC_USDT", window_seconds=60)
    # Only the fresh trade is included.
    assert payload["taker_buy_volume"] == pytest.approx(1.0)
    assert payload["taker_source"] == "gate_trades_ws_spot"
