"""Point-in-time analytical data contract.

Only entry-time snapshots are accepted. Exit snapshots and post-outcome fields
are deliberately excluded from the indicator matrix to prevent leakage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from typing import Any, Iterable, Mapping


INDICATOR_ALIASES = {
    "rsi": ("rsi", "rsi_14"),
    "adx": ("adx", "adx_14"),
    "delta_adx": ("delta_adx", "adx_delta"),
    "di_plus": ("di_plus", "plus_di"),
    "di_minus": ("di_minus", "minus_di"),
    "atr_pct": ("atr_pct", "atr_percent"),
    "bb_width": ("bb_width", "bollinger_width"),
    "z_score": ("z_score", "zscore"),
    "macd": ("macd",),
    "macd_histogram": ("macd_histogram", "macd_hist"),
    "ema_alignment": ("ema_alignment",),
    "vwap_distance": ("vwap_distance", "vwap_distance_pct"),
    "volume_delta": ("volume_delta",),
    "taker_ratio": ("taker_ratio",),
    "orderbook_pressure": ("orderbook_pressure",),
    "spread": ("spread", "spread_pct"),
    "volume_24h": ("volume_24h",),
    "market_structure_score": ("market_structure_score",),
    "momentum_score": ("momentum_score",),
    "signal_score": ("signal_score",),
    "liquidity_score": ("liquidity_score",),
    "score_total": ("score_total", "score", "final_priority_score"),
}

REGIME_KEYS = ("market_regime", "regime", "btc_regime")


@dataclass(frozen=True)
class CanonicalObservation:
    observation_id: str
    profile_id: str
    profile_version_id: str | None
    symbol: str
    timeframe: str | None
    occurred_at: datetime
    outcome: str
    tp_hit: int
    net_pnl_pct: float | None
    regime: str | None
    policy_key: str
    indicators: Mapping[str, float | None]
    source: str


def finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def extract_indicators(
    snapshot: Mapping[str, Any], requested: Iterable[str] | None = None
) -> dict[str, float | None]:
    names = list(requested) if requested is not None else list(INDICATOR_ALIASES)
    normalized: dict[str, float | None] = {}
    for name in names:
        aliases = INDICATOR_ALIASES.get(name, (name,))
        value = next((snapshot[key] for key in aliases if key in snapshot), None)
        normalized[name] = finite_number(value)
    return normalized


def extract_regime(snapshot: Mapping[str, Any]) -> str | None:
    for key in REGIME_KEYS:
        value = snapshot.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def canonical_hash(observations: Iterable[CanonicalObservation]) -> str:
    payload = []
    for item in observations:
        raw = asdict(item)
        raw["occurred_at"] = item.occurred_at.isoformat()
        payload.append(raw)
    payload.sort(key=lambda row: (row["occurred_at"], row["observation_id"]))
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def policy_key(row: Mapping[str, Any]) -> str:
    policy = {
        "barrier_contract_version": row.get("barrier_contract_version"),
        "tp_pct": finite_number(row.get("tp_pct")),
        "sl_pct": finite_number(row.get("sl_pct")),
        "timeout_candles": row.get("timeout_candles"),
        "direction": row.get("direction"),
    }
    return hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
