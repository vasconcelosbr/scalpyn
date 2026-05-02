"""Order Flow Service — taker buy/sell volume aggregation from Gate.io spot trades.

Contracts:
  taker_ratio   = taker_buy_volume / (taker_buy_volume + taker_sell_volume)
                  → range: [0, 1] or None,  equilibrium = 0.5
                  None is returned when the window is degenerate
                  (no taker activity at all) or the computed ratio falls
                  outside the plausibility bounds. This is the canonical
                  industry definition (a.k.a. "Buy Volume Ratio") and
                  matches every consumer in this codebase
                  (futures_pipeline_scorer, blocking_rules,
                  market_data_service.is_valid_data, feature_engine).
  buy_pressure  = taker_buy_volume / max(taker_buy_volume + taker_sell_volume, 1e-9)
                  → range: [0, 1],  equilibrium = 0.5
                  Same numeric value as taker_ratio; kept for backward
                  compatibility with profiles/score rules that already
                  reference ``buy_pressure`` explicitly.

History: until Task #82, taker_ratio was defined as ``buy / sell`` with
range (0, 5]. That definition produced absurd values (~1e9) on
one-sided windows even after the #72 epsilon-floor fix and conflicted
with the rest of the codebase, which already assumed [0, 1]
(futures_pipeline_scorer thresholds 0.50/0.55/0.65,
blocking_rules._MIN_TAKER_RATIO=0.4, market_data_service guard
``0 <= taker_ratio <= 1``). #82 unifies the formula on the canonical
[0, 1] scale.

Sources (Task #171):
  Primary:  Redis sorted set ``trades_buffer:{NORMALIZED_SYMBOL}``
            populated by the Gate Spot WebSocket handler
            (``handle_spot_trades``). Source tag: ``gate_trades_ws``.
  Fallback: Gate.io ``GET /spot/trades`` REST. Source tag: ``gate_io_trades``.
            Used when the WS buffer is empty (e.g. WS feature flag off,
            leader replica still connecting after a deploy, Redis outage,
            or symbol simply has zero trades in the window).

Window: caller-controlled ``window_seconds`` (default 60 s; the Celery
``compute_indicators`` paths now pass 300 s, aligned to
``handle_spot_trades.TRADE_BUFFER_TTL_SECONDS = 360 s``).

If no trades are available in either source → all fields are None (no fallback, no proxy).
"""

import json
import logging
import time
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

WINDOW_SECONDS: int = 60

# Plausibility bounds for the persisted taker_ratio (= buy_vol / (buy_vol + sell_vol)).
# Mirrors the predicate in `app.services.indicator_validity` (0 <= v <= 1).
# A real ratio is bounded to [0, 1] by construction; anything outside
# is a corrupted feed (collector accidentally writing volume or
# market_cap into the ratio field) and must NOT be persisted.
TAKER_RATIO_MIN: float = 0.0      # inclusive lower bound (0 = all sells)
TAKER_RATIO_MAX: float = 1.0      # inclusive upper bound (1 = all buys)


def safe_taker_ratio(
    symbol: str,
    window_seconds: int,
    buy_vol: float,
    sell_vol: float,
) -> Optional[float]:
    """Compute taker_ratio = buy_vol / (buy_vol + sell_vol) with degeneracy guards.

    Public helper shared with ``app.scoring.layer_order_flow`` so both
    collector paths (spot + futures L5) apply the same formula.

    Returns ``None`` whenever the window has no taker activity at all
    (buy_vol == 0 AND sell_vol == 0) or the result falls outside the
    plausibility bounds [0, 1] — the latter cannot happen with valid
    inputs, so it acts as a defense-in-depth assertion against bad
    feeds. Callers are expected to persist ``None`` so downstream rule
    evaluators mark the indicator as SKIPPED instead of FAIL.

    A one-sided window (e.g. only buy trades) is no longer treated as
    invalid: with the canonical formula ``buy / (buy + sell)`` it just
    produces 1.0 (or 0.0 for sell-only), which is a real, bounded
    signal of total directional flow. We still emit a low-volume
    diagnostic so operators can spot symbols that consistently produce
    one-sided windows (often a thin-liquidity or feed problem).
    """
    total_vol = buy_vol + sell_vol
    if total_vol <= 0:
        return None

    if buy_vol <= 0 or sell_vol <= 0:
        # Not invalid under the new formula, but worth noting: a pure
        # 0.0 / 1.0 means the entire window had taker flow on one side.
        logger.warning(
            "[OrderFlow] one-sided %ds window for %s "
            "(buy=%.8f sell=%.8f) — taker_ratio=%.4f",
            window_seconds, symbol, buy_vol, sell_vol,
            buy_vol / total_vol,
        )

    raw = buy_vol / total_vol
    if not (TAKER_RATIO_MIN <= raw <= TAKER_RATIO_MAX):
        logger.warning(
            "[OrderFlow] taker_ratio out of plausibility range for %s: %.4f "
            "(buy=%.8f sell=%.8f) — discarding, persisting None",
            symbol, raw, buy_vol, sell_vol,
        )
        return None

    return round(raw, 6)


def _aggregate_buy_sell(
    symbol: str,
    window_seconds: int,
    buy_vol: float,
    sell_vol: float,
    source: str,
) -> Dict[str, Optional[Any]]:
    """Build the public return dict from already-aggregated buy/sell volumes.

    Shared between the Redis-buffer path and the REST fallback so both
    apply the exact same plausibility guards and rounding.
    """
    total_vol = buy_vol + sell_vol
    if total_vol < 0:
        logger.error("[OrderFlow] negative total volume for %s — skipping", symbol)
        return {
            "taker_buy_volume":  None,
            "taker_sell_volume": None,
            "taker_ratio":       None,
            "buy_pressure":      None,
            "volume_delta":      None,
            "taker_source":      source,
            "taker_window":      f"{window_seconds}s",
        }

    taker_ratio = safe_taker_ratio(symbol, window_seconds, buy_vol, sell_vol)
    buy_pressure = buy_vol / max(total_vol, 1e-9)

    if not (0.0 <= buy_pressure <= 1.0):
        logger.error(
            "[OrderFlow] buy_pressure outside [0,1] for %s: %.6f — discarding",
            symbol, buy_pressure,
        )
        return {
            "taker_buy_volume":  None,
            "taker_sell_volume": None,
            "taker_ratio":       None,
            "buy_pressure":      None,
            "volume_delta":      None,
            "taker_source":      source,
            "taker_window":      f"{window_seconds}s",
        }

    return {
        "taker_buy_volume":  round(buy_vol,      8),
        "taker_sell_volume": round(sell_vol,     8),
        "taker_ratio":       taker_ratio,
        "buy_pressure":      round(buy_pressure, 6),
        "volume_delta":      round(buy_vol - sell_vol, 8),
        "taker_source":      source,
        "taker_window":      f"{window_seconds}s",
    }


async def _read_buffer(
    symbol: str,
    window_seconds: int,
    market_type: str = "spot",
) -> Optional[Dict[str, Optional[Any]]]:
    """Aggregate taker flow from the Redis ``trades_buffer:{market_type}:*`` sorted set.

    Returns the same dict shape as :func:`get_order_flow_data` when the
    buffer has at least one trade in ``[now - window_seconds, +inf]``.
    Returns ``None`` to signal "buffer unavailable or empty — caller
    should try the REST fallback".

    Symbol normalisation **must** match
    ``event_handlers._trades_buffer_key`` exactly — divergence here is
    review risk #1 and silently makes the buffer look empty even when
    full.
    """
    from .redis_client import get_async_redis
    from ..exchange_adapters.gate_adapter import GateAdapter

    try:
        redis = await get_async_redis()
    except Exception as exc:
        logger.debug("[OrderFlow] redis client unavailable: %s", exc)
        return None
    if redis is None:
        return None

    pair = GateAdapter._normalize_symbol(symbol)
    key = f"trades_buffer:{market_type}:{pair}"
    cutoff_ms = (time.time() - window_seconds) * 1_000.0

    try:
        # ZRANGEBYSCORE is sufficient — we don't need scores back, just
        # the JSON payloads, and the score is also stored inside each
        # member as ``"t"``.
        members = await redis.zrangebyscore(key, cutoff_ms, "+inf")
    except Exception as exc:
        logger.warning("[OrderFlow] redis zrangebyscore failed for %s: %s", symbol, exc)
        return None

    if not members:
        return None

    buy_vol = 0.0
    sell_vol = 0.0
    included = 0

    for raw in members:
        # Handler appends a trailing ``|<id>`` suffix so duplicate
        # (side, amount, ts) members do not collide in the sorted set.
        # JSON parse from the start ignores the suffix because
        # ``json.loads`` reads only the first JSON value, which is what
        # we wrote; but safer is to slice off the suffix manually.
        try:
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            cut = text.rfind("|")
            payload_str = text[:cut] if cut > 0 else text
            t = json.loads(payload_str)
        except (UnicodeDecodeError, ValueError) as exc:
            logger.debug("[OrderFlow] skipping malformed buffer entry for %s: %s", symbol, exc)
            continue
        try:
            amount = float(t.get("a", 0) or 0)
        except (TypeError, ValueError):
            continue
        if amount < 0:
            logger.warning("[OrderFlow] negative amount in buffer for %s: %s", symbol, amount)
            continue
        side = t.get("s")
        if side == "buy":
            buy_vol += amount
            included += 1
        elif side == "sell":
            sell_vol += amount
            included += 1

    if included == 0:
        return None

    ws_source = f"gate_trades_ws_{market_type}"
    return _aggregate_buy_sell(
        symbol=symbol,
        window_seconds=window_seconds,
        buy_vol=buy_vol,
        sell_vol=sell_vol,
        source=ws_source,
    )


async def get_order_flow_data(
    symbol: str,
    window_seconds: int = WINDOW_SECONDS,
    market_type: str = "spot",
) -> Dict[str, Optional[Any]]:
    """Fetch recent trades and aggregate taker flow metrics.

    Reads first from the WS-fed Redis buffer (key prefix
    ``trades_buffer:{market_type}:``); falls back to the REST
    ``GET /spot/trades`` path when the buffer is empty/unavailable.

    Args:
        symbol:         Pair in Gate.io format, e.g. "BTC_USDT".
        window_seconds: Look-back window in seconds (default 60).
        market_type:    Market namespace — ``"spot"`` (default) or
                        ``"futures"``.  Controls which Redis buffer is
                        read and which REST endpoint is hit on fallback.
        symbol:         Pair in Gate.io format, e.g. "BTC_USDT".
        window_seconds: Look-back window in seconds (default 60).

    Returns:
        {
            "taker_buy_volume":  float | None,   # base-asset buy volume in window
            "taker_sell_volume": float | None,   # base-asset sell volume in window
            "taker_ratio":       float | None,   # buy / (buy + sell)  (0 → 1)
            "buy_pressure":      float | None,   # buy / (buy + sell)  (0 → 1)
            "volume_delta":      float | None,   # buy_vol - sell_vol  (base asset)
            "taker_source":      str,            # e.g. "gate_trades_ws_spot" | "gate_io_trades"
            "taker_window":      str,
        }
    """
    # ── Buffer-first read ────────────────────────────────────────────────
    buffered = await _read_buffer(symbol, window_seconds, market_type=market_type)
    if buffered is not None:
        return buffered

    # Buffer empty / unavailable → REST fallback. Logged at INFO so the
    # diagnostic shows up in default log dashboards (Task #180); a sharp
    # rise in this line in production typically means the WS leader is
    # down or the trades buffer TTL has expired.
    logger.info(
        "[OrderFlow] buffer empty for %s market=%s (window=%ds) — using REST fallback",
        symbol, market_type, window_seconds,
    )

    empty = {
        "taker_buy_volume":  None,
        "taker_sell_volume": None,
        "taker_ratio":       None,
        "buy_pressure":      None,
        "volume_delta":      None,
        "taker_source":      "gate_io_trades",
        "taker_window":      f"{window_seconds}s",
    }

    try:
        from ..exchange_adapters.gate_adapter import GateAdapter

        pair   = GateAdapter._normalize_symbol(symbol)
        trades = await GateAdapter._public_get(
            f"{GateAdapter.SPOT_BASE}/spot/trades",
            params={"currency_pair": pair, "limit": "500"},
        )
        if not trades:
            return empty

        cutoff_ms = (time.time() - window_seconds) * 1_000

        buy_vol  = 0.0
        sell_vol = 0.0
        included = 0

        for t in trades:
            ts_ms = float(t.get("create_time_ms", 0) or 0)
            if ts_ms < cutoff_ms:
                continue

            amount = float(t.get("amount", 0) or 0)
            if amount < 0:
                logger.warning("[OrderFlow] negative amount in trade for %s: %s", symbol, amount)
                continue

            if t.get("side") == "buy":
                buy_vol += amount
            elif t.get("side") == "sell":
                sell_vol += amount

            included += 1

        if included == 0:
            logger.debug("[OrderFlow] no trades in %ds window for %s", window_seconds, symbol)
            return empty

        return _aggregate_buy_sell(
            symbol=symbol,
            window_seconds=window_seconds,
            buy_vol=buy_vol,
            sell_vol=sell_vol,
            source="gate_io_trades",
        )

    except Exception as exc:
        logger.warning("[OrderFlow] failed to fetch trades for %s: %s", symbol, exc)
        return empty
