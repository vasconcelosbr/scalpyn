"""Symbol, day, and regime concentration checks."""

from __future__ import annotations

from collections import Counter
from typing import Sequence

from ..data_contract import CanonicalObservation


def concentration_metrics(
    observations: Sequence[CanonicalObservation],
) -> dict[str, float | int]:
    if not observations:
        return {"n_trades": 0, "n_symbols": 0, "n_days": 0, "max_symbol_concentration": 1.0}
    symbols = Counter(item.symbol for item in observations)
    days = {item.occurred_at.date() for item in observations}
    regimes = Counter(item.regime or "UNKNOWN" for item in observations)
    return {
        "n_trades": len(observations),
        "n_symbols": len(symbols),
        "n_days": len(days),
        "n_regimes": len(regimes),
        "min_regime_samples": min(regimes.values()),
        "max_symbol_concentration": max(symbols.values()) / len(observations),
    }
