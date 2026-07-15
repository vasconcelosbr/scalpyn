"""Versioned economic targets and EV calculations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutcomeCosts:
    gross_return_pct: float
    fees_pct: float = 0.0
    spread_pct: float = 0.0
    slippage_pct: float = 0.0
    funding_pct: float = 0.0

    @property
    def net_return_pct(self) -> float:
        return self.gross_return_pct - self.fees_pct - self.spread_pct - self.slippage_pct - self.funding_pct


def economic_label(outcome: OutcomeCosts, noise_band_pct: float) -> int | None:
    if noise_band_pct < 0:
        raise ValueError("economic_noise_band_must_be_non_negative")
    if abs(outcome.net_return_pct) < noise_band_pct:
        return None
    return int(outcome.net_return_pct > 0)


def expected_value_pct(
    *,
    p_tp: float,
    avg_tp_pct: float,
    p_sl: float,
    avg_sl_pct: float,
    p_timeout: float,
    avg_timeout_pct: float,
    costs_pct: float,
) -> float:
    probability_sum = p_tp + p_sl + p_timeout
    if any(p < 0 for p in (p_tp, p_sl, p_timeout)) or abs(probability_sum - 1.0) > 1e-9:
        raise ValueError("outcome_probabilities_must_sum_to_one")
    return p_tp * avg_tp_pct + p_sl * avg_sl_pct + p_timeout * avg_timeout_pct - costs_pct
