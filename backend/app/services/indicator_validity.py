"""Indicator validity helper for the decision engine.

A rule must NEVER fire FAIL when the underlying indicator is missing or
implausible — that produces false negatives that block trades for data
reasons rather than for genuine signal reasons. Instead, callers mark
the rule as SKIPPED and continue.

This module centralises the validity check so every evaluator (block
rules, entry triggers, signals) applies the same definition.

It also exposes `validate_macd_histogram()`, a pure diagnostic function
that computes a structured consistency report for the MACD Histogram.
"""

from __future__ import annotations

import json
import logging
import math
from enum import Enum
from typing import Any, List, Optional, Tuple


logger = logging.getLogger(__name__)


class RuleStatus(str, Enum):
    """Tristate result of a single rule evaluation."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"


class SkipReason(str, Enum):
    """Standard reasons emitted when a rule is SKIPPED."""

    INDICATOR_NOT_AVAILABLE = "indicator_not_available"
    INDICATOR_INVALID_VALUE = "indicator_invalid_value"


# Indicators where additional plausibility constraints apply. Values that
# are technically present but logically impossible (e.g. taker_ratio==0)
# are treated as missing data, not as evidence against the rule.
#
# Each entry maps an indicator name (lowercased) to a predicate that
# returns True when the value is plausible. Predicates assume the value
# has already passed the None/NaN screening.
_PLAUSIBILITY_RULES = {
    # taker_ratio = taker_buy_volume / (taker_buy_volume + taker_sell_volume).
    # Canonical "Buy Volume Ratio" definition adopted in #82 — bounded
    # to [0, 1] by construction. Anything outside this range is a
    # corrupted feed (collector accidentally writing volume or
    # market_cap into the ratio field) or a stale row from the legacy
    # buy/sell scale (Task #72 era). Treat as invalid_value rather
    # than letting an absurd number drive the rule outcome
    # (regression: SUI showed taker_ratio == 8.98e9 in prod;
    # PENGU_USDT showed 3.28e11 even after #72).
    # The endpoints 0 (all sells) and 1 (all buys) are valid signals
    # of total directional flow and remain accepted.
    "taker_ratio": lambda v: 0 <= v <= 1,
    "volume_spike": lambda v: v > 0,
    # ADX is bounded [0, 100] but a literal 0 means the calculation has
    # not converged yet (insufficient candles).
    "adx": lambda v: v > 0,
    # Bollinger band width and spread are widths/percentages, must be > 0.
    "bb_width": lambda v: v > 0,
    "spread": lambda v: v > 0,
    "spread_pct": lambda v: v > 0,
    # RSI is bounded [0, 100]; out-of-range values indicate corrupt data.
    "rsi": lambda v: 0 <= v <= 100,
    # macd_histogram can legitimately be zero (crossover point) or
    # negative; only NaN/None should disqualify it. No extra rule.
}


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


def is_valid(value: Any, indicator_name: Optional[str] = None) -> Tuple[bool, Optional[SkipReason]]:
    """Return (valid, skip_reason) for an indicator value.

    Args:
        value: Raw value from the indicator payload.
        indicator_name: Indicator name used to look up plausibility rules.

    Returns:
        (True, None)            if the value is usable.
        (False, SkipReason.X)   if the value should cause the rule to be
                                marked SKIPPED with the given reason.
    """
    if value is None:
        return False, SkipReason.INDICATOR_NOT_AVAILABLE

    if _is_nan(value):
        return False, SkipReason.INDICATOR_NOT_AVAILABLE

    key = (indicator_name or "").strip().lower()
    rule = _PLAUSIBILITY_RULES.get(key)
    if rule is not None:
        try:
            if not rule(float(value)):
                return False, SkipReason.INDICATOR_INVALID_VALUE
        except (TypeError, ValueError):
            return False, SkipReason.INDICATOR_INVALID_VALUE

    return True, None


def log_skipped(indicator: str, value: Any, reason: SkipReason) -> None:
    """Emit a single-line structured JSON log when a rule is skipped."""
    if isinstance(value, float) and math.isnan(value):
        json_value: Any = "NaN"
    elif value is None or isinstance(value, (str, int, float, bool)):
        json_value = value
    else:
        json_value = repr(value)
    payload = {
        "event": "indicator_skipped",
        "indicator": indicator,
        "value": json_value,
        "status": RuleStatus.SKIPPED.value,
        "reason": reason.value,
    }
    logger.info(json.dumps(payload, default=str))


def validate_macd_histogram(
    close_prices: List[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict:
    """Compute and validate the MACD Histogram for a series of close prices.

    This is a pure function (no DB access).  It replicates the same EWM
    calculation used by FeatureEngine._calc_macd so that the diagnostic
    result is always consistent with what the pipeline stores.

    Args:
        close_prices: Ordered list of closing prices (oldest → newest).
                      Minimum required: slow + signal candles (e.g. 35 for
                      the default 26+9 configuration).
        fast:         EMA fast period (default 12).
        slow:         EMA slow period (default 26).
        signal:       Signal-line EMA period (default 9).

    Returns:
        Dict with keys:
          histogram_value, histogram_sign, momentum_direction,
          momentum_strength, consistency_status, signal_quality,
          diagnostic_message, details.

    Raises:
        ValueError: if close_prices has fewer than (slow + signal) entries.
    """
    min_required = slow + signal
    if len(close_prices) < min_required:
        raise ValueError(
            f"validate_macd_histogram requires at least {min_required} close prices "
            f"(slow={slow} + signal={signal}); got {len(close_prices)}."
        )

    try:
        import pandas as pd
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("pandas/numpy are required for validate_macd_histogram") from exc

    close = pd.Series(close_prices, dtype=float)
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    current = float(histogram.iloc[-1])
    macd_val = float(macd_line.iloc[-1])
    sig_val = float(signal_line.iloc[-1])

    hist_prev: Optional[float] = float(histogram.iloc[-2]) if len(histogram) >= 2 else None

    # Use the 10 candles BEFORE the current one as the statistical baseline.
    # Including the current value in the window makes z_score mathematically
    # bounded at sqrt(n-1)=3.0 for a single outlier, so the condition > 3
    # would never fire.  The prior-10 window establishes the established
    # distribution so that a genuine outlier current value is detectable.
    prior = histogram.iloc[-11:-1] if len(histogram) >= 11 else histogram.iloc[:-1]
    mean_10 = float(prior.mean()) if len(prior) >= 1 else 0.0
    std_10 = float(prior.std(ddof=0)) if len(prior) >= 2 else 0.0

    epsilon = std_10 * 0.1 if std_10 > 0 else 1e-6

    if hist_prev is None:
        momentum_direction = "flat"
        momentum_strength = "flat"
    else:
        if current > hist_prev + epsilon:
            momentum_direction = "up"
        elif current < hist_prev - epsilon:
            momentum_direction = "down"
        else:
            momentum_direction = "flat"

        if abs(current) > abs(hist_prev) + epsilon:
            momentum_strength = "strengthening"
        elif abs(current) < abs(hist_prev) - epsilon:
            momentum_strength = "weakening"
        else:
            momentum_strength = "flat"

    if current > 0:
        histogram_sign = "positive"
    elif current < 0:
        histogram_sign = "negative"
    else:
        histogram_sign = "zero"

    consistency_status = "valid"
    z_score: Optional[float] = None

    if std_10 > 0:
        z_score = min(abs(current - mean_10) / std_10, 10.0)
        if z_score > 3:
            consistency_status = "inconsistent"

    if consistency_status != "inconsistent":
        if mean_10 != 0 and (current * mean_10 < 0):
            consistency_status = "inconsistent"

    if consistency_status != "inconsistent":
        if abs(mean_10) > 0 and abs(current) > 10 * abs(mean_10):
            consistency_status = "inconsistent"

    if current > 0:
        signal_quality = "strong_bullish" if momentum_strength == "strengthening" else "weak_bullish"
    elif current < 0:
        signal_quality = "strong_bearish" if momentum_strength == "strengthening" else "weak_bearish"
    else:
        signal_quality = "neutral"

    _sign_word = {"positive": "positive", "negative": "negative", "zero": "at zero"}.get(histogram_sign, histogram_sign)
    _prev_str = f" (prev {hist_prev:.6f})" if hist_prev is not None else ""
    if consistency_status == "inconsistent":
        diagnostic_message = (
            f"MACD Histogram {_sign_word} and {momentum_strength}{_prev_str}. "
            f"Inconsistency detected — value may not reflect chart reality. "
            f"Direction: {momentum_direction}. Signal quality: {signal_quality}."
        )
    else:
        diagnostic_message = (
            f"MACD Histogram {_sign_word} and {momentum_strength}{_prev_str}. "
            f"Direction: {momentum_direction}. Signal quality: {signal_quality}."
        )

    return {
        "histogram_value": round(current, 8),
        "histogram_sign": histogram_sign,
        "momentum_direction": momentum_direction,
        "momentum_strength": momentum_strength,
        "consistency_status": consistency_status,
        "signal_quality": signal_quality,
        "diagnostic_message": diagnostic_message,
        "details": {
            "macd_line": round(macd_val, 8),
            "signal_line": round(sig_val, 8),
            "histogram_prev": round(hist_prev, 8) if hist_prev is not None else None,
            "histogram_mean_10": round(mean_10, 8),
            "histogram_std_10": round(std_10, 8),
            "z_score": round(z_score, 4) if z_score is not None else None,
        },
    }
