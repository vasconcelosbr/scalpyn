"""Versioned, observational entry-risk feature contract.

The legacy scalar is preserved byte-for-byte at the public boundary.  The two
new concepts intentionally have no aggregate formula in v1: persisting raw
components is evidence collection, not trading authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from typing import Any, Mapping

import numpy as np
import pandas as pd


SCHEMA_VERSION = "entry_risk_features_v1"
LEGACY_FORMULA_VERSION = "entry_exhaustion_legacy_v1"
LEGACY_NORMALIZATION_VERSION = "entry_exhaustion_legacy_norm_v1"
WINDOW_CANONICALIZATION_VERSION = "ohlcv_window_v1"
SOURCE_TIMEFRAME = "5m"
WINDOW_SIZE = 50
logger = logging.getLogger(__name__)

OBSERVATIONAL_ONLY_FIELDS = frozenset({
    "momentum_intensity_score",
    "exhaustion_risk_score",
})
INDICATOR_GOVERNANCE = {
    name: {
        "status": "OBSERVATIONAL_ONLY",
        "entry_trigger_allowed": False,
        "block_rule_allowed": False,
        "scoring_allowed": False,
    }
    for name in OBSERVATIONAL_ONLY_FIELDS
}


def assert_no_observational_execution_fields(profile_config: Mapping[str, Any]) -> None:
    """Reject v1 candidate scores anywhere in an executable profile config."""
    violations: set[str] = set()
    legacy_references: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if (
                    key in {"field", "indicator", "feature", "name"}
                    and str(item) in OBSERVATIONAL_ONLY_FIELDS
                ):
                    violations.add(str(item))
                if (
                    key in {"field", "indicator", "feature", "name"}
                    and str(item) == "entry_exhaustion_score"
                ):
                    legacy_references.add(str(item))
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(profile_config)
    if violations:
        raise ValueError(
            "OBSERVATIONAL_ONLY fields cannot be used by execution: "
            + ", ".join(sorted(violations))
        )
    if legacy_references:
        logger.warning(
            "OBSERVATIONAL_LEGACY field used operationally: %s",
            ", ".join(sorted(legacy_references)),
        )


def pending_entry_risk_payload(
    feature_metadata: Mapping[str, Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Durable input for the post-commit capture reconciler."""
    normalized: dict[str, dict[str, Any]] = {}
    for name, raw in (feature_metadata or {}).items():
        if not isinstance(raw, Mapping):
            continue
        ts = raw.get("timestamp") or raw.get("ts")
        normalized[str(name)] = {
            "timestamp": ts.isoformat() if isinstance(ts, datetime) else ts,
            "timeframe": raw.get("timeframe"),
            "group": raw.get("group") or raw.get("source_group"),
            "source": raw.get("source"),
            "stale": bool(raw.get("stale", False)),
            "observed_timeframes": raw.get("observed_timeframes") or [],
            "timeframe_conflict": bool(raw.get("timeframe_conflict", False)),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "capture_input": {"feature_metadata": normalized},
        "contract_status": {
            "status": "PENDING",
            "entry_risk_contract_valid": False,
            "entry_risk_eligible_for_training": False,
            "reason_codes": [],
        },
    }

_CANDIDATE_FEATURES = {
    "momentum_intensity": (
        "adx", "adx_slope_3", "macd_histogram_pct", "macd_hist_slope_3",
        "bb_width",
    ),
    "extension": (
        "vwap_distance_pct", "ema9_distance_pct", "ema21_distance_pct", "atr_pct",
    ),
    "deceleration": (
        "rsi_slope_3", "rsi_slope_5", "macd_hist_slope_3", "macd_hist_slope_5",
        "adx_slope_3",
    ),
    "confirmation_loss": (
        "di_plus_minus_diff", "volume_delta", "taker_ratio", "flow_strength",
        "vwap_reclaim_bool", "higher_highs_5", "higher_lows_5",
    ),
}


def _finite(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _legacy_component(
    *,
    raw_value: float | None,
    normalized_value: float,
    weight: float,
    fallback_used: bool = False,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "raw_value": raw_value,
        "normalized_value": normalized_value,
        "weight": weight,
        "contribution": normalized_value * weight,
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "source_timeframe": SOURCE_TIMEFRAME,
        "data_source": "ohlcv",
    }


def calculate_legacy_entry_exhaustion(
    df: pd.DataFrame,
    *,
    atr_period: int = 14,
) -> dict[str, Any]:
    """Return the exact legacy score plus a reconstructible decomposition."""
    if len(df) < WINDOW_SIZE:
        return {
            "score": None,
            "components": {},
            "status": "INSUFFICIENT_CANDLES",
            "reason_codes": ["MISSING_COMPONENT"],
        }

    close = pd.to_numeric(df["close"], errors="coerce")
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    if "volume" in df.columns:
        volume = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    elif "base_volume" in df.columns:
        volume = pd.to_numeric(df["base_volume"], errors="coerce").fillna(0.0)
    else:
        volume = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)

    close_last = float(close.iloc[-1])
    if not _finite(close_last) or close_last <= 0:
        return {
            "score": None,
            "components": {},
            "status": "INVALID_CURRENT_CLOSE",
            "reason_codes": ["MISSING_COMPONENT"],
        }

    raw_roc5: float | None = None
    roc5_score = 50.0
    roc5_fallback = True
    if len(df) >= 6:
        close_5 = float(close.iloc[-6])
        if _finite(close_5) and close_5 > 0:
            raw_roc5 = (close_last - close_5) / close_5 * 100
            clipped = max(-20.0, min(20.0, raw_roc5))
            roc5_score = (clipped + 20.0) / 40.0 * 100
            roc5_fallback = False

    raw_roc20: float | None = None
    roc20_score = 50.0
    roc20_fallback = True
    if len(df) >= 21:
        close_20 = float(close.iloc[-21])
        if _finite(close_20) and close_20 > 0:
            raw_roc20 = (close_last - close_20) / close_20 * 100
            clipped = max(-50.0, min(50.0, raw_roc20))
            roc20_score = (clipped + 50.0) / 100.0 * 100
            roc20_fallback = False

    rolling_high = float(high.iloc[-WINDOW_SIZE:].max())
    raw_distance: float | None = None
    distance_score = 50.0
    distance_fallback = True
    if _finite(rolling_high) and rolling_high > 0:
        raw_distance = (close_last - rolling_high) / rolling_high * 100
        clipped = max(-20.0, min(0.0, raw_distance))
        distance_score = (clipped + 20.0) / 20.0 * 100
        distance_fallback = False

    range_series = (high - low).clip(lower=0)
    current_range = float(range_series.iloc[-1])
    atr_period = max(1, int(atr_period))
    atr_value = float(range_series.rolling(window=min(atr_period, len(df))).mean().iloc[-1])
    raw_expansion: float | None = None
    expansion_score = 50.0
    expansion_fallback = True
    if _finite(atr_value) and atr_value > 0:
        raw_expansion = current_range / atr_value
        expansion_score = max(0.0, min(5.0, raw_expansion)) / 5.0 * 100
        expansion_fallback = False

    volume_window = volume.iloc[-WINDOW_SIZE:].values
    current_volume = float(volume.iloc[-1])
    raw_volume_percentile: float | None = None
    volume_score = 50.0
    volume_fallback = True
    if len(volume_window) and _finite(current_volume):
        raw_volume_percentile = float(np.mean(volume_window <= current_volume))
        volume_score = raw_volume_percentile * 100
        volume_fallback = False

    components = {
        "acceleration_5": _legacy_component(
            raw_value=raw_roc5, normalized_value=roc5_score, weight=0.20,
            fallback_used=roc5_fallback, fallback_reason="INVALID_ROC5_BASE" if roc5_fallback else None,
        ),
        "acceleration_20": _legacy_component(
            raw_value=raw_roc20, normalized_value=roc20_score, weight=0.20,
            fallback_used=roc20_fallback, fallback_reason="INVALID_ROC20_BASE" if roc20_fallback else None,
        ),
        "distance_from_local_high_50": _legacy_component(
            raw_value=raw_distance, normalized_value=distance_score, weight=0.30,
            fallback_used=distance_fallback, fallback_reason="INVALID_LOCAL_HIGH" if distance_fallback else None,
        ),
        "candle_expansion_ratio": _legacy_component(
            raw_value=raw_expansion, normalized_value=expansion_score, weight=0.15,
            fallback_used=expansion_fallback, fallback_reason="INVALID_ATR" if expansion_fallback else None,
        ),
        "volume_percentile_50": _legacy_component(
            raw_value=raw_volume_percentile, normalized_value=volume_score, weight=0.15,
            fallback_used=volume_fallback, fallback_reason="INVALID_VOLUME" if volume_fallback else None,
        ),
    }
    score = round(sum(float(item["contribution"]) for item in components.values()), 1)
    return {"score": score, "components": components, "status": "VALID", "reason_codes": []}


def legacy_entry_exhaustion_score(
    df: pd.DataFrame,
    *,
    atr_period: int = 14,
) -> float | None:
    return calculate_legacy_entry_exhaustion(df, atr_period=atr_period)["score"]


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value is not None:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def closed_candle_window(df: pd.DataFrame, entry_at: datetime) -> pd.DataFrame:
    """Select exactly the last 50 5m candles closed no later than entry."""
    if df.empty or "time" not in df.columns:
        return df.iloc[0:0].copy()
    times = pd.to_datetime(df["time"], utc=True, errors="coerce")
    cutoff = pd.Timestamp(_as_utc(entry_at))
    closed = df.loc[(times + pd.Timedelta(minutes=5)) <= cutoff].copy()
    closed["time"] = times.loc[closed.index]
    return closed.sort_values("time").tail(WINDOW_SIZE).reset_index(drop=True)


def candle_window_hash(df: pd.DataFrame) -> str | None:
    if len(df) != WINDOW_SIZE:
        return None
    rows: list[dict[str, Any]] = []
    for row in df.itertuples(index=False):
        item: dict[str, Any] = {"time": _as_utc(getattr(row, "time", None)).isoformat()}
        for key in ("open", "high", "low", "close", "volume", "quote_volume"):
            value = getattr(row, key, None)
            item[key] = format(float(value), ".17g") if _finite(value) else None
        rows.append(item)
    payload = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _feature_component(
    name: str,
    features: Mapping[str, Any],
    metadata: Mapping[str, Mapping[str, Any]],
    entry_at: datetime,
) -> dict[str, Any]:
    value = features.get(name)
    meta = metadata.get(name) if isinstance(metadata.get(name), Mapping) else {}
    source_timeframe = meta.get("timeframe")
    available = value is not None
    reasons: list[str] = []
    if not available:
        reasons.append("MISSING_COMPONENT")
    if available and source_timeframe != SOURCE_TIMEFRAME:
        reasons.append("TIMEFRAME_MISMATCH")
    observed_timeframes = sorted({
        str(item) for item in (meta.get("observed_timeframes") or []) if item
    })
    if bool(meta.get("timeframe_conflict")) or len(observed_timeframes) > 1:
        reasons.append("TIMEFRAME_CONFLICT")
    source_timestamp = meta.get("timestamp") or meta.get("ts")
    parsed_source_timestamp = _as_utc(source_timestamp)
    if available and parsed_source_timestamp is None:
        reasons.append("SOURCE_TIMESTAMP_MISSING")
    elif parsed_source_timestamp and parsed_source_timestamp > _as_utc(entry_at):
        reasons.append("SOURCE_TIMESTAMP_AFTER_ENTRY")
    if bool(meta.get("stale")):
        reasons.append("STALE_FEATURE")
    return {
        "raw_value": value,
        "normalized_value": None,
        "weight": None,
        "contribution": None,
        "available": available,
        "source_timestamp": source_timestamp,
        "source_timeframe": source_timeframe,
        "observed_timeframes": observed_timeframes,
        "timeframe_conflict": "TIMEFRAME_CONFLICT" in reasons,
        "data_source": meta.get("source") or meta.get("group"),
        "stale": bool(meta.get("stale", False)),
        "reason_codes": reasons,
    }


def _candle_candidate_component(
    legacy_component: Mapping[str, Any],
    *,
    source_timestamp: str | None,
) -> dict[str, Any]:
    """Expose a candle-derived raw value without reusing legacy weights."""
    available = legacy_component.get("raw_value") is not None
    return {
        "raw_value": legacy_component.get("raw_value"),
        "normalized_value": None,
        "weight": None,
        "contribution": None,
        "available": available,
        "source_timestamp": source_timestamp,
        "source_timeframe": SOURCE_TIMEFRAME,
        "observed_timeframes": [SOURCE_TIMEFRAME],
        "timeframe_conflict": False,
        "data_source": "ohlcv",
        "stale": False,
        "reason_codes": [] if available else ["MISSING_COMPONENT"],
    }


def build_entry_risk_contract(
    *,
    candles: pd.DataFrame,
    features: Mapping[str, Any],
    feature_metadata: Mapping[str, Mapping[str, Any]],
    symbol: str,
    exchange: str | None,
    market_type: str | None,
    entry_at: datetime,
    decision_at: datetime | None,
    profile_id: str | None,
    profile_name: str | None,
    profile_family: str | None,
    profile_version_id: str | None,
    regime: Mapping[str, Any] | None,
) -> dict[str, Any]:
    window = closed_candle_window(candles, entry_at)
    legacy = calculate_legacy_entry_exhaustion(window)
    window_hash = candle_window_hash(window)
    reason_codes: set[str] = set(legacy.get("reason_codes") or [])
    if window_hash is None:
        reason_codes.add("CANDLE_HASH_MISSING")
    if len(window) != WINDOW_SIZE:
        reason_codes.add("CANDLE_CUTOFF_MISMATCH")
    resolved_family = profile_family or "UNKNOWN"
    if resolved_family == "UNKNOWN":
        reason_codes.add("PROFILE_FAMILY_UNKNOWN")
    if not regime or not regime.get("regime"):
        reason_codes.add("REGIME_UNKNOWN")

    momentum = {
        name: _feature_component(name, features, feature_metadata, entry_at)
        for name in _CANDIDATE_FEATURES["momentum_intensity"]
    }
    candle_source_timestamp = (
        window.iloc[-1]["time"].isoformat() if len(window) else None
    )
    for name in (
        "acceleration_5",
        "acceleration_20",
        "candle_expansion_ratio",
        "volume_percentile_50",
        "distance_from_local_high_50",
    ):
        if name in legacy["components"]:
            momentum[name] = _candle_candidate_component(
                legacy["components"][name],
                source_timestamp=candle_source_timestamp,
            )

    risk_dimensions = {
        dimension: {
            name: _feature_component(name, features, feature_metadata, entry_at)
            for name in names
        }
        for dimension, names in _CANDIDATE_FEATURES.items()
        if dimension != "momentum_intensity"
    }
    if "distance_from_local_high_50" in legacy["components"]:
        risk_dimensions["extension"]["distance_from_local_high_50"] = momentum["distance_from_local_high_50"]
    for name in ("acceleration_5", "acceleration_20"):
        if name in momentum:
            risk_dimensions["deceleration"][name] = momentum[name]

    for component in list(momentum.values()) + [
        item for dimension in risk_dimensions.values() for item in dimension.values()
    ]:
        reason_codes.update(component.get("reason_codes") or [])

    captured_at = datetime.now(timezone.utc)
    status = "VALID" if not reason_codes else "INVALID" if {
        "CANDLE_HASH_MISSING", "CANDLE_CUTOFF_MISMATCH"
    } & reason_codes else "PARTIAL"
    last_open = _as_utc(window.iloc[-1]["time"]) if len(window) else None
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at.isoformat(),
        "context": {
            "symbol": symbol,
            "exchange": exchange,
            "market_type": market_type,
            "entry_timestamp": _as_utc(entry_at).isoformat(),
            "decision_timestamp": _as_utc(decision_at).isoformat() if decision_at else None,
            "profile_id": profile_id,
            "profile_name": profile_name,
            "profile_family": resolved_family,
            "profile_version_id": profile_version_id,
            "regime": dict(regime or {}),
        },
        "candle_window": {
            "timeframe": SOURCE_TIMEFRAME,
            "closed_candle": True,
            "window_size": len(window),
            "window_start": _as_utc(window.iloc[0]["time"]).isoformat() if len(window) else None,
            "window_end": last_open.isoformat() if last_open else None,
            "candle_cutoff": (last_open + timedelta(minutes=5)).isoformat() if last_open else None,
            "candle_window_hash": window_hash,
            "canonicalization_version": WINDOW_CANONICALIZATION_VERSION,
        },
        "legacy": {
            "entry_exhaustion_score": legacy["score"],
            "status": "OBSERVATIONAL_LEGACY",
            "legacy": True,
            "deprecated_for_risk_decision": True,
            "semantic_role": "momentum_extension_legacy",
            "operational_effect": False,
            "formula_version": LEGACY_FORMULA_VERSION,
            "normalization_version": LEGACY_NORMALIZATION_VERSION,
            "components": legacy["components"],
        },
        "momentum_intensity": {
            "momentum_intensity_score": None,
            "status": "MONITOR_ONLY",
            "operational_effect": False,
            "formula_version": None,
            "normalization_version": None,
            "components": momentum,
        },
        "exhaustion_risk": {
            "exhaustion_risk_score": None,
            "status": "MONITOR_ONLY",
            "operational_effect": False,
            "formula_version": None,
            "normalization_version": None,
            "dimensions": risk_dimensions,
        },
        "contract_status": {
            "status": status,
            "entry_risk_contract_valid": status == "VALID",
            "entry_risk_eligible_for_training": False,
            "point_in_time_valid": not bool({
                "CANDLE_CUTOFF_MISMATCH",
                "TIMEFRAME_MISMATCH",
                "SOURCE_TIMESTAMP_MISSING",
                "SOURCE_TIMESTAMP_AFTER_ENTRY",
            } & reason_codes),
            "reconstructible": legacy["score"] is not None and window_hash is not None,
            "has_stale_components": "STALE_FEATURE" in reason_codes,
            "reason_codes": sorted(reason_codes),
        },
    }
