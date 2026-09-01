from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Sequence


def parse_gate_spot_candle(
    candle: Sequence[Any],
) -> Dict[str, float | bool | datetime | None]:
    """Normalize Gate.io spot candlesticks.

    Gate spot candles are returned as:
    [timestamp, quote_volume, close, high, low, open, base_volume, ...]

    Returns:
        Dict with keys time (datetime), open/high/low/close (float),
        volume (base asset float), quote_volume (quote asset float), and
        is_closed (bool | None). Gate currently returns the completion flag
        at index 7; ``None`` preserves compatibility with older payloads that
        did not expose it.
    """
    close = float(candle[2])
    quote_volume = float(candle[1])

    if len(candle) > 6 and candle[6] not in (None, ""):
        base_volume = float(candle[6])
    else:
        base_volume = quote_volume / close if close > 0 else 0.0

    closed_raw = candle[7] if len(candle) > 7 else None
    is_closed = None
    if isinstance(closed_raw, bool):
        is_closed = closed_raw
    elif closed_raw is not None:
        normalized = str(closed_raw).strip().lower()
        if normalized in {"true", "1"}:
            is_closed = True
        elif normalized in {"false", "0"}:
            is_closed = False

    return {
        "time": datetime.fromtimestamp(int(candle[0]), tz=timezone.utc),
        "volume": base_volume,
        "quote_volume": quote_volume,
        "close": close,
        "high": float(candle[3]),
        "low": float(candle[4]),
        "open": float(candle[5]),
        "is_closed": is_closed,
    }
