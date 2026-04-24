"""Indicator validity helper for the decision engine.

A rule must NEVER fire FAIL when the underlying indicator is missing or
implausible — that produces false negatives that block trades for data
reasons rather than for genuine signal reasons. Instead, callers mark
the rule as SKIPPED and continue.

This module centralises the validity check so every evaluator (block
rules, entry triggers, signals) applies the same definition.
"""

from __future__ import annotations

import logging
import math
from enum import Enum
from typing import Any, Optional, Tuple


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
    # Ratios cannot be zero or negative — a zero ratio means the source
    # feed is missing one side, not that buyers/sellers are absent.
    "taker_ratio": lambda v: v > 0,
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
    """Emit a single-line structured log when a rule is skipped."""
    logger.info(
        '{"indicator": "%s", "value": %r, "status": "SKIPPED", "reason": "%s"}',
        indicator,
        value,
        reason.value,
    )
