from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Sequence


def parse_gate_spot_candle(candle: Sequence[Any]) -> Dict[str, float | datetime]:
    """Normalize Gate.io spot candlesticks.

    Gate spot candles are returned as:
    [timestamp, quote_volume, close, high, low, open, base_volume, ...]
    """
    close = float(candle[2])
    quote_volume = float(candle[1])

    if len(candle) > 6 and candle[6] not in (None, ""):
        base_volume = float(candle[6])
    else:
        base_volume = quote_volume / close if close > 0 else 0.0

    return {
        "time": datetime.fromtimestamp(int(candle[0]), tz=timezone.utc),
        "volume": base_volume,
        "quote_volume": quote_volume,
        "close": close,
        "high": float(candle[3]),
        "low": float(candle[4]),
        "open": float(candle[5]),
    }
