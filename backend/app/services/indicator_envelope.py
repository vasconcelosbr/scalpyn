"""Indicator Envelope — Robust data structure for all indicators with metadata.

This module implements the core architecture for reliable indicator handling,
ensuring full traceability, confidence scoring, and staleness validation.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class IndicatorStatus(Enum):
    """Status of an indicator value."""
    PASS = "PASS"           # Valid value, meets threshold
    FAIL = "FAIL"           # Valid value, fails threshold
    NO_DATA = "NO_DATA"     # No data available
    ERROR = "ERROR"         # Error during calculation
    STALE = "STALE"         # Data too old to be reliable


class DataSource(Enum):
    """Source of indicator data."""
    BINANCE = "binance"               # Real Binance API data
    GATE = "gate"                     # Real Gate.io API data
    CANDLE_APPROX = "candle_approx"   # Approximation from candles (low confidence)
    DERIVED = "derived"               # Calculated from other indicators
    MERGED = "merged"                 # Merged from multiple sources


# Confidence scores by source
CONFIDENCE_MAP = {
    DataSource.BINANCE: 0.95,        # Highest - direct aggTrades, orderbook
    DataSource.GATE: 0.85,           # High - reliable but may have gaps
    DataSource.MERGED: 0.80,         # Good - combined sources
    DataSource.DERIVED: 0.90,        # High if dependencies are reliable
    DataSource.CANDLE_APPROX: 0.40,  # Low - approximation only
}

# Staleness penalties by age
STALENESS_THRESHOLDS = {
    "fresh": (0, 60, 1.0),          # < 1min: no penalty
    "recent": (60, 300, 0.8),       # 1-5min: light penalty
    "stale": (300, 600, 0.5),       # 5-10min: medium penalty
    "critical": (600, float('inf'), 0.2),  # > 10min: critical penalty
}


@dataclass
class IndicatorEnvelope:
    """Robust envelope for indicator values with full metadata.

    All indicators in the system must be wrapped in this envelope to ensure:
    - Traceability of data source
    - Confidence scoring
    - Staleness validation
    - Error handling
    - Dependency tracking
    """

    # Identification
    name: str                           # ex: "rsi", "volume_24h_usdt"
    category: str                       # "liquidity", "momentum", "structure", "flow", "volatility"

    # Value and Status
    value: Optional[float] = None       # None if not available
    status: IndicatorStatus = IndicatorStatus.NO_DATA

    # Traceability
    source: DataSource = DataSource.GATE
    source_detail: str = ""             # ex: "binance_trades_500", "gate_ticker"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    collection_latency_ms: int = 0      # latency of data collection

    # Reliability
    confidence: float = 0.5             # 0.0 - 1.0
    valid: bool = False                 # False if stale, error, or low confidence

    # Metadata for debugging
    raw_value: Any = None               # value before processing
    error_msg: Optional[str] = None     # if status == ERROR
    threshold_used: Optional[float] = None  # threshold applied (if applicable)

    # Dependencies (for derived indicators)
    dependencies: List[str] = field(default_factory=list)

    # Validation context
    min_candles_required: Optional[int] = None
    actual_candles: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "category": self.category,
            "value": self.value,
            "status": self.status.value,
            "source": self.source.value,
            "source_detail": self.source_detail,
            "timestamp": self.timestamp.isoformat(),
            "collection_latency_ms": self.collection_latency_ms,
            "confidence": round(self.confidence, 4),
            "valid": self.valid,
            "error_msg": self.error_msg,
            "threshold_used": self.threshold_used,
            "dependencies": self.dependencies,
            "min_candles_required": self.min_candles_required,
            "actual_candles": self.actual_candles,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IndicatorEnvelope":
        """Create from dictionary."""
        return cls(
            name=data["name"],
            category=data["category"],
            value=data.get("value"),
            status=IndicatorStatus(data["status"]),
            source=DataSource(data["source"]),
            source_detail=data.get("source_detail", ""),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            collection_latency_ms=data.get("collection_latency_ms", 0),
            confidence=data.get("confidence", 0.5),
            valid=data.get("valid", False),
            error_msg=data.get("error_msg"),
            threshold_used=data.get("threshold_used"),
            dependencies=data.get("dependencies", []),
            min_candles_required=data.get("min_candles_required"),
            actual_candles=data.get("actual_candles"),
        )


def wrap_indicator(
    name: str,
    category: str,
    value: Optional[float],
    source: DataSource,
    timestamp: datetime,
    source_detail: str = "",
    raw_value: Any = None,
    min_candles_required: Optional[int] = None,
    actual_candles: Optional[int] = None,
    dependencies: Optional[List[str]] = None,
    collection_latency_ms: int = 0,
) -> IndicatorEnvelope:
    """Wrap an indicator value in a robust envelope with automatic validation.

    Args:
        name: Indicator name (e.g., "rsi")
        category: Category (liquidity, momentum, structure, flow, volatility)
        value: Calculated value (None if unavailable)
        source: Data source
        timestamp: When data was collected/calculated
        source_detail: Additional source info
        raw_value: Original value before processing
        min_candles_required: Minimum candles needed for calculation
        actual_candles: Actual candles used
        dependencies: List of indicator names this depends on
        collection_latency_ms: Collection latency in milliseconds

    Returns:
        IndicatorEnvelope with confidence and staleness validation applied
    """
    # Base confidence from source
    confidence = CONFIDENCE_MAP.get(source, 0.5)

    # Calculate age
    now = datetime.now(timezone.utc)
    age_seconds = (now - timestamp).total_seconds()

    # Apply staleness penalty
    status = IndicatorStatus.NO_DATA if value is None else IndicatorStatus.PASS
    valid = value is not None

    for level, (min_age, max_age, penalty) in STALENESS_THRESHOLDS.items():
        if min_age <= age_seconds < max_age:
            confidence *= penalty
            if level == "critical":
                status = IndicatorStatus.STALE
                valid = False
            break

    # Check candle sufficiency
    if min_candles_required and actual_candles:
        if actual_candles < min_candles_required:
            logger.warning(
                f"Indicator {name}: insufficient candles "
                f"(need {min_candles_required}, have {actual_candles})"
            )
            status = IndicatorStatus.ERROR
            valid = False
            confidence *= 0.5

    return IndicatorEnvelope(
        name=name,
        category=category,
        value=value,
        status=status,
        source=source,
        source_detail=source_detail,
        timestamp=timestamp,
        collection_latency_ms=collection_latency_ms,
        confidence=confidence,
        valid=valid,
        raw_value=raw_value,
        dependencies=dependencies or [],
        min_candles_required=min_candles_required,
        actual_candles=actual_candles,
    )


def get_staleness_level(timestamp: datetime) -> str:
    """Get staleness level for a timestamp."""
    age_seconds = (datetime.now(timezone.utc) - timestamp).total_seconds()

    for level, (min_age, max_age, _) in STALENESS_THRESHOLDS.items():
        if min_age <= age_seconds < max_age:
            return level

    return "critical"


def apply_threshold(
    envelope: IndicatorEnvelope,
    operator: str,
    threshold_value: float,
) -> IndicatorEnvelope:
    """Apply threshold to indicator and update status.

    Args:
        envelope: Indicator envelope
        operator: Comparison operator (">=", "<=", ">", "<", "==")
        threshold_value: Threshold value

    Returns:
        Updated envelope with threshold applied
    """
    if envelope.value is None or not envelope.valid:
        envelope.status = IndicatorStatus.NO_DATA
        return envelope

    envelope.threshold_used = threshold_value

    if operator == ">=":
        passes = envelope.value >= threshold_value
    elif operator == "<=":
        passes = envelope.value <= threshold_value
    elif operator == ">":
        passes = envelope.value > threshold_value
    elif operator == "<":
        passes = envelope.value < threshold_value
    elif operator == "==":
        passes = abs(envelope.value - threshold_value) < 1e-6
    else:
        logger.error(f"Unknown operator: {operator}")
        envelope.status = IndicatorStatus.ERROR
        envelope.valid = False
        return envelope

    envelope.status = IndicatorStatus.PASS if passes else IndicatorStatus.FAIL
    return envelope
