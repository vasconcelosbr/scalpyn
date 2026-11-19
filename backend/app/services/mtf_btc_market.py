"""Display-only BTC/USDT quote. Never participates in MTF decisions or vetoes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import time

import httpx
from .market_data_service import GATE_SPOT_URL, GATE_TICKERS_URL
from ..utils.gate_market_data import parse_gate_spot_candle

# Requested display intervals and Gate.io's minute-candle protocol, not strategy thresholds.
WINDOWS = {"4h": 14400, "1h": 3600, "5m": 300}
MINUTE = 60
_cache = None
_lock = asyncio.Lock()


def _positive(value):
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def snapshot(ticker, candles, *, now, max_age_seconds):
    """Compare the Gate ticker with the minute close at/before each rolling boundary."""
    # MTF_BTC_MARKET_DISPLAY_ONLY: same exchange as the trading market.
    result = {
        "symbol": "BTC_USDT", "currency": "USDT", "source": "Gate.io",
        "status": "UNAVAILABLE", "price": None, "as_of": None,
        "fetched_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "method": "TICKER_VS_MINUTE_CLOSE", "timestamp_kind": "REQUEST_TIME",
        "changes": {tf: {"pct": None, "reference_price": None, "reference_at": None} for tf in WINDOWS},
    }
    if not isinstance(ticker, dict):
        return result
    price = _positive(ticker.get("price"))
    try:
        at = datetime.fromisoformat(str(ticker.get("time")).replace("Z", "+00:00"))
        if at.tzinfo is None:
            return result
        timestamp = at.timestamp()
    except (ValueError, TypeError):
        return result
    if price is None or timestamp > now:
        return result
    result["as_of"] = at.astimezone(timezone.utc).isoformat()
    if now - timestamp > max_age_seconds:
        result["status"] = "STALE"
        return result
    result["price"] = price
    closes = {}
    conflicting = set()
    for row in candles if isinstance(candles, list) else []:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            candle = parse_gate_spot_candle(row)
            if candle["is_closed"] is False:
                continue
            start, close = candle["time"].timestamp(), _positive(candle["close"])
        except (ValueError, TypeError, OverflowError, OSError):
            continue
        if start is None or close is None or start % MINUTE != 0:
            continue
        end = int(start) + MINUTE
        if end in closes and closes[end] != close:
            conflicting.add(end)
        closes[end] = close
    for tf, seconds in WINDOWS.items():
        # Never use a candle whose close is later than the requested reference.
        boundary = math.floor((timestamp - seconds) / MINUTE) * MINUTE
        reference = closes.get(boundary)
        if reference is None or boundary in conflicting:
            continue
        change = (price / reference - 1) * 100
        if not math.isfinite(change):
            continue
        result["changes"][tf] = {
            "pct": change,
            "reference_price": reference,
            "reference_at": datetime.fromtimestamp(boundary, timezone.utc).isoformat(),
        }
    result["status"] = "READY" if all(v["pct"] is not None for v in result["changes"].values()) else "PARTIAL"
    return result


async def _fetch():
    now = datetime.now(timezone.utc)
    start = int(now.timestamp()) - max(WINDOWS.values()) - 2 * MINUTE
    async with httpx.AsyncClient(timeout=10.0) as client:
        responses = await asyncio.gather(
            client.get(GATE_TICKERS_URL, params={"currency_pair": "BTC_USDT"}),
            client.get(GATE_SPOT_URL, params={
                "currency_pair": "BTC_USDT", "interval": "1m", "from": start, "to": int(now.timestamp()),
            }),
            return_exceptions=True,
        )
    values = []
    for response in responses:
        try:
            if isinstance(response, Exception):
                raise response
            response.raise_for_status()
            values.append(response.json())
        except (httpx.HTTPError, ValueError):
            values.append(None)
    # Gate tickers have no trade timestamp. Freeze the original request time;
    # cache reads must never turn an old quote into a newly observed quote.
    tickers, candles = values
    quote = next((v for v in tickers if isinstance(v, dict) and v.get("currency_pair") == "BTC_USDT"), None) if isinstance(tickers, list) else None
    ticker = {"price": quote.get("last"), "time": now.isoformat()} if quote else None
    return ticker, candles


async def get_snapshot(cfg):
    global _cache
    # Shared public quote; no user data in the cache. Re-evaluate age on every read.
    async with _lock:
        if _cache is None or time.monotonic() - _cache[0] >= cfg.poll_seconds:
            ticker, candles = await _fetch()
            _cache = (time.monotonic(), ticker, candles)
        _, ticker, candles = _cache
    return snapshot(ticker, candles, now=time.time(),
                    max_age_seconds=cfg.poll_seconds * cfg.stale_tf_multiplier)
