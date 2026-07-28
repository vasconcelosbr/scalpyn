"""Symbol, day, and regime concentration checks."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from ..data_contract import CanonicalObservation


def _effective_count(counts: Counter) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    concentration = sum((count / total) ** 2 for count in counts.values())
    return 1.0 / concentration if concentration > 0 else 0.0


def concentration_metrics(
    observations: Sequence[CanonicalObservation],
) -> dict[str, float | int]:
    if not observations:
        return {
            "n_trades": 0,
            "n_symbols": 0,
            "n_days": 0,
            "max_symbol_concentration": 1.0,
            "max_day_concentration": 1.0,
            "effective_symbols": 0.0,
            "effective_days": 0.0,
        }
    symbols = Counter(item.symbol for item in observations)
    days = Counter(item.occurred_at.date() for item in observations)
    regimes = Counter(item.regime or "UNKNOWN" for item in observations)
    return {
        "n_trades": len(observations),
        "n_symbols": len(symbols),
        "n_days": len(days),
        "n_regimes": len(regimes),
        "min_regime_samples": min(regimes.values()),
        "max_symbol_concentration": max(symbols.values()) / len(observations),
        "max_day_concentration": max(days.values()) / len(observations),
        "effective_symbols": _effective_count(symbols),
        "effective_days": _effective_count(days),
    }
