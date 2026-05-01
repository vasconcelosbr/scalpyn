"""Envelope dataclass + enums + confidence/staleness tables.

An ``IndicatorEnvelope`` wraps a single indicator value with the metadata the
robust pipeline needs to reason about its trustworthiness:

    * ``status``      — VALID / DEGRADED / NO_DATA / INVALID
    * ``source``      — origin of the value (Gate trades, candles, merged, ...)
    * ``timestamp``   — when the value was computed
    * ``confidence``  — base confidence from CONFIDENCE_MAP, then reduced by
                        a piecewise staleness penalty derived from
                        ``timestamp``.

The envelope is intentionally serialisable to JSONB so it can be persisted
verbatim in ``indicator_snapshots.indicators_json``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class IndicatorStatus(str, Enum):
    """Tristate-plus result for a single indicator value."""

    VALID = "VALID"
    DEGRADED = "DEGRADED"
    NO_DATA = "NO_DATA"
    INVALID = "INVALID"


class DataSource(str, Enum):
    """Provenance of an indicator value."""

    GATE_TRADES = "gate_trades"
    GATE_CANDLES = "gate_candles"
    GATE_TICKER = "gate_ticker"
    GATE_ORDERBOOK = "gate_orderbook"
    BINANCE_TRADES = "binance_trades"
    BINANCE_CANDLES = "binance_candles"
    BINANCE_TICKER = "binance_ticker"
    MERGED = "merged"
    CANDLE_FALLBACK = "candle_fallback"
    DERIVED = "derived"
    UNKNOWN = "unknown"


# Base confidence per source. Real-trade sources score highest because they
# reflect actual taker activity rather than a candle-shape proxy.
CONFIDENCE_MAP: Dict[DataSource, float] = {
    DataSource.GATE_TRADES: 1.00,
    DataSource.BINANCE_TRADES: 0.95,
    DataSource.GATE_ORDERBOOK: 0.90,
    DataSource.GATE_TICKER: 0.85,
    DataSource.BINANCE_TICKER: 0.85,
    DataSource.GATE_CANDLES: 0.85,
    DataSource.BINANCE_CANDLES: 0.80,
    DataSource.MERGED: 0.85,
    DataSource.DERIVED: 0.80,
    DataSource.CANDLE_FALLBACK: 0.40,
    DataSource.UNKNOWN: 0.30,
}


# Piecewise staleness multiplier. ``(min_age_s, max_age_s, multiplier)``.
# Applied to the base confidence; > 300s is essentially unusable.
STALENESS_PENALTY = (
    (0.0, 60.0, 1.00),
    (60.0, 180.0, 0.85),
    (180.0, 300.0, 0.50),
    (300.0, math.inf, 0.10),
)


def _staleness_multiplier(age_seconds: float) -> float:
    if age_seconds < 0:
        # Future timestamps treated as fresh; clock skew should not penalise.
        age_seconds = 0.0
    for lo, hi, mult in STALENESS_PENALTY:
        if lo <= age_seconds < hi:
            return mult
    return STALENESS_PENALTY[-1][2]


def _coerce_source(source: Any) -> DataSource:
    if isinstance(source, DataSource):
        return source
    if isinstance(source, str):
        try:
            return DataSource(source)
        except ValueError:
            return DataSource.UNKNOWN
    return DataSource.UNKNOWN


def _is_nan(value: Any) -> bool:
    return isinstance(value, float) and math.isnan(value)


@dataclass
class IndicatorEnvelope:
    """A single indicator value plus provenance / confidence metadata."""

    name: str
    value: Any
    status: IndicatorStatus
    source: DataSource
    timestamp: datetime
    confidence: float
    base_confidence: float
    staleness_seconds: float
    notes: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """JSONB-friendly representation."""
        return {
            "name": self.name,
            "value": self.value,
            "status": self.status.value,
            "source": self.source.value,
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat(),
            "confidence": round(self.confidence, 4),
            "base_confidence": round(self.base_confidence, 4),
            "staleness_seconds": round(self.staleness_seconds, 3),
            "notes": self.notes,
            "extras": self.extras or {},
        }

    @property
    def is_usable(self) -> bool:
        """True when the indicator can participate in scoring."""
        return self.status in (IndicatorStatus.VALID, IndicatorStatus.DEGRADED)


def wrap_indicator(
    name: str,
    value: Any,
    source: Any,
    timestamp: Optional[datetime] = None,
    *,
    now: Optional[datetime] = None,
    notes: Optional[str] = None,
    extras: Optional[Dict[str, Any]] = None,
    forced_status: Optional[IndicatorStatus] = None,
) -> IndicatorEnvelope:
    """Build a fully populated :class:`IndicatorEnvelope`.

    The resulting envelope:
      * is ``NO_DATA`` when ``value`` is ``None`` / ``NaN``;
      * is ``INVALID`` when ``forced_status`` says so;
      * is ``DEGRADED`` when staleness multiplier dropped the confidence
        below 1.0 of the base value; and
      * inherits the appropriate base confidence from ``CONFIDENCE_MAP``.
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    age = (now - timestamp).total_seconds()

    src_enum = _coerce_source(source)
    base_conf = CONFIDENCE_MAP.get(src_enum, CONFIDENCE_MAP[DataSource.UNKNOWN])

    if forced_status is IndicatorStatus.INVALID:
        status = IndicatorStatus.INVALID
        confidence = 0.0
    elif value is None or _is_nan(value):
        status = IndicatorStatus.NO_DATA
        confidence = 0.0
    else:
        mult = _staleness_multiplier(age)
        confidence = round(base_conf * mult, 4)
        if forced_status is not None:
            status = forced_status
        elif mult < 1.0:
            status = IndicatorStatus.DEGRADED
        else:
            status = IndicatorStatus.VALID

    return IndicatorEnvelope(
        name=name,
        value=value,
        status=status,
        source=src_enum,
        timestamp=timestamp,
        confidence=confidence,
        base_confidence=base_conf,
        staleness_seconds=max(0.0, age),
        notes=notes,
        extras=dict(extras or {}),
    )


def envelope_from_dict(payload: Dict[str, Any]) -> IndicatorEnvelope:
    """Reverse of :meth:`IndicatorEnvelope.to_dict` — used in tests."""
    ts = payload["timestamp"]
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return IndicatorEnvelope(
        name=payload["name"],
        value=payload["value"],
        status=IndicatorStatus(payload["status"]),
        source=DataSource(payload["source"]),
        timestamp=ts,
        confidence=float(payload["confidence"]),
        base_confidence=float(payload.get("base_confidence", payload["confidence"])),
        staleness_seconds=float(payload.get("staleness_seconds", 0.0)),
        notes=payload.get("notes"),
        extras=dict(payload.get("extras") or {}),
    )


__all__ = [
    "CONFIDENCE_MAP",
    "STALENESS_PENALTY",
    "DataSource",
    "IndicatorEnvelope",
    "IndicatorStatus",
    "asdict",
    "envelope_from_dict",
    "wrap_indicator",
]
