"""Point-in-time price-position metrics computed from closed candles only."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd


RECENT_HIGH_WINDOWS: Dict[str, int] = {
    "5m": 1,
    "15m": 3,
    "30m": 6,
    "1h": 12,
}
BREAKOUT_REFERENCE_INDICATORS: Dict[str, str] = {
    window: f"recent_high_{window}_distance_pct"
    for window in RECENT_HIGH_WINDOWS
}


def _closed_candles(
    df: Optional[pd.DataFrame],
    *,
    timeframe_minutes: int,
    as_of: Optional[datetime] = None,
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    frame = df.copy()
    if "time" not in frame.columns:
        return frame.reset_index(drop=True)

    timestamps = pd.to_datetime(frame["time"], utc=True, errors="coerce")
    cutoff = pd.Timestamp(as_of or datetime.now(timezone.utc))
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")
    completed = timestamps + pd.Timedelta(minutes=timeframe_minutes) <= cutoff
    frame = frame.loc[completed & timestamps.notna()].copy()
    frame["_timestamp"] = timestamps.loc[frame.index]
    return frame.sort_values("_timestamp").reset_index(drop=True)


def _finite_positive(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _distance_pct(price: Any, reference: Any) -> Optional[float]:
    price_value = _finite_positive(price)
    reference_value = _finite_positive(reference)
    if price_value is None or reference_value is None:
        return None
    return round((price_value - reference_value) / reference_value * 100.0, 4)


def _reference_time(row: pd.Series) -> Optional[str]:
    value = row.get("_timestamp")
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()


def _daily_vwap(frame: pd.DataFrame) -> Optional[float]:
    if frame.empty:
        return None
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    typical = (high + low + close) / 3.0
    dates = frame["_timestamp"].dt.date if "_timestamp" in frame else None
    if dates is None:
        numerator = (typical * volume).cumsum()
        denominator = volume.cumsum()
    else:
        work = pd.DataFrame({"weighted": typical * volume, "volume": volume, "date": dates})
        numerator = work.groupby("date")["weighted"].cumsum()
        denominator = work.groupby("date")["volume"].cumsum()
    value = (numerator / denominator.replace(0, np.nan)).iloc[-1]
    return round(float(value), 8) if pd.notna(value) and value > 0 else None


def calculate_price_position(
    df_5m: pd.DataFrame,
    *,
    df_1m: Optional[pd.DataFrame] = None,
    as_of: Optional[datetime] = None,
    bollinger_period: int = 20,
    bollinger_deviation: float = 2.0,
) -> Dict[str, Any]:
    """Return additive metrics anchored to the latest completed 5m close.

    Recent levels exclude the base candle. Fixed windows are expressed as
    completed 5m bars, while ``price_change_1m_pct`` uses completed 1m bars
    aligned no later than the close of the selected 5m base candle.
    """

    metric_names = [
        "ema5_distance_pct", "ema9_distance_pct", "ema21_distance_pct",
        "ema50_distance_pct", "ema200_distance_pct",
        "vwap_distance_pct",
        "bb_upper_distance_pct", "bb_middle_distance_pct", "bb_lower_distance_pct",
        *(f"recent_high_{window}_distance_pct" for window in RECENT_HIGH_WINDOWS),
        "recent_low_15m_distance_pct",
        "price_change_1m_pct", "price_change_5m_pct", "price_change_15m_pct",
    ]
    result: Dict[str, Any] = {name: None for name in metric_names}

    closed_5m = _closed_candles(df_5m, timeframe_minutes=5, as_of=as_of)
    if closed_5m.empty:
        return result

    closes = pd.to_numeric(closed_5m["close"], errors="coerce")
    base_close = _finite_positive(closes.iloc[-1])
    if base_close is None:
        return result

    for period in (5, 9, 21, 50, 200):
        if len(closes) < period:
            continue
        ema = closes.ewm(span=period, adjust=False).mean().iloc[-1]
        result[f"ema{period}_distance_pct"] = _distance_pct(base_close, ema)

    vwap = _daily_vwap(closed_5m)
    result["vwap_distance_pct"] = _distance_pct(base_close, vwap)

    if bollinger_period > 0 and len(closes) >= bollinger_period:
        middle = closes.rolling(window=bollinger_period).mean().iloc[-1]
        std = closes.rolling(window=bollinger_period).std(ddof=0).iloc[-1]
        if pd.notna(middle) and pd.notna(std):
            result["bb_upper_distance_pct"] = _distance_pct(base_close, middle + bollinger_deviation * std)
            result["bb_middle_distance_pct"] = _distance_pct(base_close, middle)
            result["bb_lower_distance_pct"] = _distance_pct(base_close, middle - bollinger_deviation * std)

    prior = closed_5m.iloc[:-1]
    for window, bars in RECENT_HIGH_WINDOWS.items():
        key = f"recent_high_{window}"
        result[f"{key}_level"] = None
        result[f"{key}_reference_time"] = None
        if len(prior) < bars:
            continue
        window_rows = prior.tail(bars)
        highs = pd.to_numeric(window_rows["high"], errors="coerce")
        if not highs.notna().any():
            continue
        row = window_rows.loc[highs.idxmax()]
        level = _finite_positive(row.get("high"))
        result[f"{key}_level"] = level
        result[f"{key}_reference_time"] = _reference_time(row)
        result[f"{key}_distance_pct"] = _distance_pct(base_close, level)

    result["recent_low_15m_level"] = None
    result["recent_low_15m_reference_time"] = None
    if len(prior) >= 3:
        low_rows = prior.tail(3)
        lows = pd.to_numeric(low_rows["low"], errors="coerce")
        if lows.notna().any():
            row = low_rows.loc[lows.idxmin()]
            level = _finite_positive(row.get("low"))
            result["recent_low_15m_level"] = level
            result["recent_low_15m_reference_time"] = _reference_time(row)
            result["recent_low_15m_distance_pct"] = _distance_pct(base_close, level)

    if len(closes) >= 2:
        result["price_change_5m_pct"] = _distance_pct(base_close, closes.iloc[-2])
    if len(closes) >= 4:
        result["price_change_15m_pct"] = _distance_pct(base_close, closes.iloc[-4])

    closed_1m = _closed_candles(df_1m, timeframe_minutes=1, as_of=as_of)
    if not closed_1m.empty and "_timestamp" in closed_5m:
        base_end = closed_5m["_timestamp"].iloc[-1] + pd.Timedelta(minutes=5)
        closed_1m = closed_1m.loc[
            closed_1m["_timestamp"] + pd.Timedelta(minutes=1) <= base_end
        ]
    if len(closed_1m) >= 2:
        closes_1m = pd.to_numeric(closed_1m["close"], errors="coerce")
        result["price_change_1m_pct"] = _distance_pct(closes_1m.iloc[-1], closes_1m.iloc[-2])

    return result


def resolve_breakout_indicator(reference_window: Any) -> Optional[str]:
    return BREAKOUT_REFERENCE_INDICATORS.get(str(reference_window or "").strip())


__all__ = [
    "BREAKOUT_REFERENCE_INDICATORS",
    "RECENT_HIGH_WINDOWS",
    "calculate_price_position",
    "resolve_breakout_indicator",
]
