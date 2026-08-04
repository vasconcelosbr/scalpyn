"""Read-only causal lineage resolver for historical L3_PROFILE rows.

Historical ``shadow_trades`` snapshots are immutable.  Their per-feature
envelopes remain available through ``decisions_log.metrics.indicators_snapshot``.
This module resolves that parent evidence only while assembling a dataset; it
never writes the derived timestamps or adjusted label anchor back to a shadow.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping, Sequence


TIMESTAMP_ALIASES: tuple[str, ...] = ("ts", "timestamp")

# Canonical feature -> historical snapshot aliases.
SOURCE_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "atr_pct": ("atr_percent",),
    "volume_24h_base": ("volume_24h",),
    "ema_distance_pct": ("ema9_ema21_distance_pct",),
    "vwap_distance_pct": ("price_vs_vwap_pct",),
    "volume_spike": ("volume_spike_ratio",),
    "taker_ratio": ("taker_buy_pressure_5m",),
    "adx_acceleration": ("adx_slope_1",),
    "macd_histogram_slope": ("macd_hist_slope_1",),
}

# Model features engineered by ``feature_extractor.extract_features``.  Each
# inner tuple is a set of aliases for one required dependency.
DERIVED_SOURCE_DEPENDENCIES: dict[str, tuple[tuple[str, ...], ...]] = {
    "macd_histogram_pct": (("macd_histogram",), ("close", "price")),
    "macd_histogram_slope": (
        ("macd_histogram_slope", "macd_hist_slope_1"),
        ("close", "price"),
    ),
    "flow_strength": (("taker_ratio", "taker_buy_pressure_5m"), ("volume_delta",)),
    "trend_alignment": (("ema9_gt_ema21",), ("ema50_gt_ema200",)),
    "momentum_strength": (
        ("macd_histogram",),
        ("close", "price"),
        ("adx",),
    ),
    "delta_normalized": (("volume_delta",), ("volume_24h_usdt",)),
    "ema_distance_pct": (("ema9",), ("ema21",)),
    "ema50_distance_pct": (("close", "price"), ("ema50",)),
    "ema200_distance_pct": (("close", "price"), ("ema200",)),
    "di_trend": (("di_plus",), ("di_minus",)),
}


@dataclass(frozen=True)
class HistoricalLineageResolution:
    record: dict[str, Any] | None
    exclusion_reason: str | None
    neutralized_features: tuple[str, ...] = ()


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _same_value(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if math.isnan(float(left)) and math.isnan(float(right)):
            return True
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-12)
    return left == right


def _envelope_timestamp(envelope: Any) -> datetime | None:
    if not isinstance(envelope, Mapping):
        return None
    for key in TIMESTAMP_ALIASES:
        parsed = _as_utc(envelope.get(key))
        if parsed is not None:
            return parsed
    return None


def _find_envelope(
    indicator_snapshot: Mapping[str, Any],
    names: Sequence[str],
) -> tuple[str | None, Mapping[str, Any] | None, datetime | None]:
    for name in names:
        envelope = indicator_snapshot.get(name)
        timestamp = _envelope_timestamp(envelope)
        if isinstance(envelope, Mapping) and timestamp is not None:
            return name, envelope, timestamp
    return None, None, None


def _direct_source(
    feature_name: str,
    features_snapshot: Mapping[str, Any],
    indicator_snapshot: Mapping[str, Any],
) -> tuple[datetime | None, str | None, Any, Any]:
    names = (feature_name, *SOURCE_NAME_ALIASES.get(feature_name, ()))
    snapshot_name = next(
        (name for name in names if features_snapshot.get(name) is not None),
        None,
    )
    envelope_name, envelope, timestamp = _find_envelope(indicator_snapshot, names)
    source_group = envelope.get("source_group") if envelope else None
    envelope_value = envelope.get("value") if envelope else None
    snapshot_value = features_snapshot.get(snapshot_name) if snapshot_name else None
    return timestamp, source_group, envelope_value, snapshot_value


def _derived_source(
    feature_name: str,
    features_snapshot: Mapping[str, Any],
    indicator_snapshot: Mapping[str, Any],
    untrusted_source_groups: set[str],
) -> tuple[datetime | None, str | None]:
    dependencies = DERIVED_SOURCE_DEPENDENCIES.get(feature_name)
    if not dependencies:
        return None, "missing_source_timestamp"
    timestamps: list[datetime] = []
    for alternatives in dependencies:
        present = next(
            (name for name in alternatives if features_snapshot.get(name) is not None),
            None,
        )
        if present is None:
            return None, "derived_dependency_absent"
        _, envelope, timestamp = _find_envelope(indicator_snapshot, alternatives)
        if envelope is None or timestamp is None:
            return None, "missing_source_timestamp"
        if str(envelope.get("source_group") or "") in untrusted_source_groups:
            return None, "untrusted_source_group"
        if not _same_value(envelope.get("value"), features_snapshot.get(present)):
            return None, "source_value_mismatch"
        timestamps.append(timestamp)
    return max(timestamps), None


def resolve_historical_l3_record(
    raw_record: Mapping[str, Any],
    *,
    model_feature_columns: Iterable[str],
    contract_version: str,
    configured_neutralized_features: Iterable[str],
    untrusted_source_groups: Iterable[str],
) -> HistoricalLineageResolution:
    """Resolve one legacy row into an immutable, causal dataset projection."""
    decision_at = _as_utc(raw_record.get("decision_created_at"))
    label_event_at = _as_utc(raw_record.get("historical_label_event_at"))
    if decision_at is None:
        return HistoricalLineageResolution(None, "missing_decision_created_at")
    if label_event_at is None:
        return HistoricalLineageResolution(None, "missing_historical_label_event_at")
    if label_event_at <= decision_at:
        return HistoricalLineageResolution(None, "label_not_after_decision")

    features_snapshot = raw_record.get("features_snapshot") or {}
    indicator_snapshot = raw_record.get("decision_indicator_snapshot") or {}
    if not isinstance(features_snapshot, Mapping) or not isinstance(
        indicator_snapshot, Mapping
    ):
        return HistoricalLineageResolution(None, "invalid_indicator_snapshot")

    features_snapshot = deepcopy(dict(features_snapshot))
    configured_neutralized = {str(value) for value in configured_neutralized_features}
    untrusted_groups = {str(value) for value in untrusted_source_groups}
    neutralized: set[str] = set()
    source_times: dict[str, datetime] = {}

    for feature_name in (str(value) for value in model_feature_columns):
        if feature_name in configured_neutralized:
            neutralized.add(feature_name)
            continue

        if feature_name in DERIVED_SOURCE_DEPENDENCIES:
            timestamp, error = _derived_source(
                feature_name,
                features_snapshot,
                indicator_snapshot,
                untrusted_groups,
            )
            if error == "source_value_mismatch":
                return HistoricalLineageResolution(
                    None, f"source_value_mismatch:{feature_name}"
                )
            if timestamp is None:
                neutralized.add(feature_name)
                continue
        else:
            timestamp, source_group, envelope_value, snapshot_value = _direct_source(
                feature_name, features_snapshot, indicator_snapshot
            )
            # A feature absent from the historical snapshot is already NaN; no
            # synthetic value or timestamp is introduced.
            if snapshot_value is None:
                continue
            if str(source_group or "") in untrusted_groups:
                neutralized.add(feature_name)
                continue
            if timestamp is None:
                neutralized.add(feature_name)
                continue
            if not _same_value(envelope_value, snapshot_value):
                return HistoricalLineageResolution(
                    None, f"source_value_mismatch:{feature_name}"
                )

        if timestamp > decision_at:
            return HistoricalLineageResolution(
                None, f"feature_source_after_decision:{feature_name}"
            )
        source_times[feature_name] = timestamp

    for feature_name in neutralized:
        # Direct values are removed here.  Engineered values are additionally
        # overwritten after dataframe construction using the durable marker.
        if feature_name in features_snapshot:
            features_snapshot[feature_name] = None
        for alias in SOURCE_NAME_ALIASES.get(feature_name, ()):
            if alias in features_snapshot:
                features_snapshot[alias] = None

    if not source_times:
        return HistoricalLineageResolution(None, "no_resolved_model_feature_sources")

    record = dict(raw_record)
    record["entry_timestamp_original"] = raw_record.get("entry_timestamp")
    record["created_at_original"] = raw_record.get("created_at")
    record["holding_seconds_original"] = raw_record.get("holding_seconds")
    record["entry_timestamp"] = decision_at
    record["created_at"] = decision_at
    record["holding_seconds"] = (label_event_at - decision_at).total_seconds()
    record["label_anchor_at"] = decision_at
    record["label_resolved_at"] = label_event_at
    record["features_snapshot"] = features_snapshot
    record["feature_source_times"] = {
        key: value.isoformat() for key, value in sorted(source_times.items())
    }
    record["feature_source_at"] = max(source_times.values())
    record["historical_lineage_resolved"] = True
    record["dataset_lineage_contract_version"] = contract_version
    record["historical_neutralized_features"] = sorted(neutralized)
    return HistoricalLineageResolution(
        record,
        None,
        tuple(sorted(neutralized)),
    )

