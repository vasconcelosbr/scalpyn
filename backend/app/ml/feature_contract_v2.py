"""Canonical, versioned entry-feature contract.

Legacy aliases remain readable. New snapshots are emitted with canonical keys
and a deterministic hash over canonical JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping


FEATURE_SCHEMA_VERSION = "entry_features_v2"
FEATURE_EXTRACTOR_VERSION = "feature-engine-v2"
CAPTURE_CONTRACT_VERSION = "point-in-time-v2"


@dataclass(frozen=True)
class NativeFeatureCapture:
    snapshot: dict[str, Any]
    captured_at: datetime
    source_at: datetime | None
    source_times: dict[str, str]
    snapshot_hash: str
    feature_extractor_version: str
    feature_schema_version: str
    capture_contract_version: str
    errors: tuple[str, ...]


@dataclass(frozen=True)
class FeatureSpec:
    canonical_name: str
    aliases: tuple[str, ...]
    value_type: str
    unit: str
    scale: str
    valid_range: tuple[float, float] | None
    nullable: bool
    timeframe_behavior: str
    source: str
    freshness_sla_s: int
    aggregation: str
    derivation: str | None = None


REGISTRY: dict[str, FeatureSpec] = {
    "atr_pct": FeatureSpec("atr_pct", ("atr_percent",), "number", "percent", "0_to_100", (0, 100), False, "per_timeframe", "indicators", 900, "last"),
    "macd_signal": FeatureSpec("macd_signal", (), "category", "direction", "enum", None, True, "per_timeframe", "indicators", 900, "last"),
    "psar_trend": FeatureSpec("psar_trend", (), "category", "direction", "enum", None, True, "per_timeframe", "indicators", 900, "last"),
    "di_trend": FeatureSpec("di_trend", (), "category", "direction", "enum", None, True, "per_timeframe", "derived", 900, "last", "bullish when di_plus > di_minus; bearish when lower"),
    "volume_24h_base": FeatureSpec("volume_24h_base", ("volume_24h",), "number", "base_asset", "absolute", (0, float("inf")), True, "market_24h", "ticker", 180, "rolling_sum"),
    "volume_24h_usdt": FeatureSpec("volume_24h_usdt", (), "number", "USDT", "absolute", (0, float("inf")), True, "market_24h", "ticker", 180, "rolling_sum"),
    "bb_width": FeatureSpec("bb_width", (), "number", "ratio", "decimal", (0, 10), True, "per_timeframe", "indicators", 900, "last"),
}

_ALIASES = {alias: name for name, spec in REGISTRY.items() for alias in spec.aliases}
_DERIVED_SOURCE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "di_trend": ("di_plus", "di_minus"),
    "ema9_gt_ema50": ("ema9", "ema50"),
    "ema9_gt_ema21": ("ema9", "ema21"),
    "ema50_gt_ema200": ("ema50", "ema200"),
    "ema_full_alignment": ("ema9", "ema50", "ema200"),
}

# AUD-IR-CTR-001 (Fase 1, L07): fields whose value is an aggregate output of
# the score engine over the *rest* of the same snapshot (decisions_log's
# score_components -> l3_trade_consolidation.py), not an independently
# sourced market reading. They carry no timestamp of their own to look up --
# their freshness is already bounded by whichever REGISTRY/directional
# feature in the same snapshot they were computed from. Introduced by
# commit 90d974c3 (2026-08-03) alongside L3 profile consolidation
# (d07f7c4/5eaf5f6, 2026-08-02); the commit added a per-feature source-
# timestamp requirement without exempting these, so every L3/L3_LAB capture
# that included a consolidation score failed 100% of the time from
# 2026-08-04 onward with `missing_source_timestamp:<field>` -- collapsing
# eligible_for_training to 0% for those lanes.
_DECISION_COMPUTED_FIELDS: frozenset[str] = frozenset({
    "score", "signal_score", "momentum_score", "liquidity_score",
    "market_structure_score",
})


# ── P0-B (auditoria captura L3 2026-07-24) ───────────────────────────────────
# Bloco direcional point-in-time emitido por ``feature_engine._calc_directional_
# features``. Antes desta guarda, ``eligible_for_training`` só validava a
# presença de ``atr_pct`` (REGISTRY), então snapshots "finos" (16 chaves, sem
# nenhuma direcional) eram marcados elegíveis e contaminavam o dataset de treino
# com features NaN. Estas 9 chaves co-ocorrem em exatamente 100% das linhas de
# captura completa (L3/L3_LAB/L1_SPECTRUM) e em 0% das linhas finas — logo
# separam captura completa de captura quebrada sem rejeitar linhas saudáveis.
# Uma linha sem o bloco → ``missing_directional:*`` em ``errors`` →
# ``eligible_for_training=False`` (shadow_trade_service._create_from_decision).
REQUIRED_DIRECTIONAL_FEATURES: tuple[str, ...] = (
    "rsi_slope_3",
    "rsi_slope_5",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "adx_slope_3",
    "ema21_ema50_distance_pct",
    "di_plus_minus_diff",
    "higher_highs_5",
    "higher_lows_5",
)


def directional_capture_errors(snapshot: Mapping[str, Any]) -> list[str]:
    """Erros de contrato quando o bloco direcional não foi capturado.

    Checa PRESENÇA da chave (não valor não-nulo): um snapshot completo com
    valor ``None`` por histórico insuficiente ainda carrega a chave e é
    tratado a jusante pelo filtro de cobertura por-feature. Um snapshot fino
    não tem a chave — e é este o caso que invalida a elegibilidade.
    """
    return [
        f"missing_directional:{name}"
        for name in REQUIRED_DIRECTIONAL_FEATURES
        if name not in snapshot
    ]


def _canonical_value(name: str, value: Any, values: Mapping[str, Any]) -> Any:
    if name == "macd_signal" and isinstance(value, str):
        return {"positive": "bullish", "negative": "bearish"}.get(value.lower(), value.lower())
    if name == "psar_trend" and isinstance(value, str):
        return {"bullish": "RISING", "bearish": "FALLING"}.get(value.lower(), value.upper())
    if name == "di_trend" and value is None:
        plus, minus = values.get("di_plus"), values.get("di_minus")
        if plus is not None and minus is not None:
            return "bullish" if float(plus) > float(minus) else "bearish"
    return value


def normalize_snapshot(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    normalized: dict[str, Any] = {}
    errors: list[str] = []
    for raw_name, raw_value in snapshot.items():
        name = _ALIASES.get(raw_name, raw_name)
        if name in normalized and normalized[name] != raw_value:
            errors.append(f"conflicting_alias:{name}")
            continue
        normalized[name] = raw_value
    for name, spec in REGISTRY.items():
        value = _canonical_value(name, normalized.get(name), normalized)
        if value is not None:
            normalized[name] = value
        if value is None and not spec.nullable:
            errors.append(f"missing_required:{name}")
        if value is not None and spec.valid_range and isinstance(value, (int, float)):
            lower, upper = spec.valid_range
            if not lower <= float(value) <= upper:
                errors.append(f"out_of_range:{name}")
    return normalized, errors


def snapshot_hash(snapshot: Mapping[str, Any]) -> str:
    payload = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime | None:
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


def _feature_source_timestamps(
    snapshot: Mapping[str, Any],
    *,
    source_snapshot: Mapping[str, Any] | None = None,
    feature_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[dict[str, datetime], list[str]]:
    """Return the causal source timestamp used by every non-null feature.

    ``features_captured_at`` is intentionally not a substitute for this value:
    a worker may persist an already-known snapshot after the decision.  Every
    non-null training feature must therefore carry its own ``ts``/``timestamp``
    either in the source envelope or in the explicit metadata map.
    """
    source_snapshot = source_snapshot or {}
    feature_metadata = feature_metadata or {}
    timestamps: dict[str, datetime] = {}
    errors: list[str] = []
    for name, value in snapshot.items():
        if str(name).startswith("_") or value is None:
            continue
        if name in _DECISION_COMPUTED_FIELDS:
            continue
        lookup_names = (
            name,
            *(REGISTRY.get(name).aliases if name in REGISTRY else ()),
            *_DERIVED_SOURCE_DEPENDENCIES.get(name, ()),
        )
        envelopes = [source_snapshot.get(key) for key in lookup_names]
        metadata_entries = [feature_metadata.get(key) for key in lookup_names]
        candidates: list[Any] = []
        for envelope in envelopes:
            if isinstance(envelope, Mapping):
                candidates.extend((envelope.get("ts"), envelope.get("timestamp")))
        for metadata in metadata_entries:
            if isinstance(metadata, Mapping):
                candidates.extend((metadata.get("ts"), metadata.get("timestamp")))
        raw_timestamps = [
            candidate for candidate in candidates if candidate is not None
        ]
        parsed_timestamps = [
            parsed
            for parsed in (_parse_timestamp(candidate) for candidate in raw_timestamps)
            if parsed is not None
        ]
        if not raw_timestamps:
            errors.append(f"missing_source_timestamp:{name}")
        elif len(parsed_timestamps) != len(raw_timestamps):
            errors.append(f"invalid_source_timestamp:{name}")
        if parsed_timestamps:
            timestamps[name] = max(parsed_timestamps)
    return timestamps, errors


def feature_source_timestamp(
    snapshot: Mapping[str, Any],
    *,
    source_snapshot: Mapping[str, Any] | None = None,
    feature_metadata: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[datetime | None, list[str]]:
    """Return the latest immutable source timestamp used by the snapshot."""
    timestamps, errors = _feature_source_timestamps(
        snapshot,
        source_snapshot=source_snapshot,
        feature_metadata=feature_metadata,
    )
    if not timestamps:
        errors.append("missing_feature_source_at")
        return None, errors
    return max(timestamps.values()), errors


def capture_native_snapshot(
    snapshot: Mapping[str, Any],
    *,
    source_snapshot: Mapping[str, Any] | None = None,
    feature_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    decision_created_at: datetime | None = None,
    entry_at: datetime | None = None,
    captured_at: datetime | None = None,
) -> NativeFeatureCapture:
    captured_at = captured_at or utcnow()
    normalized, errors = normalize_snapshot(snapshot)
    # P0-B: bloco direcional é requisito de elegibilidade. Checado sobre o
    # snapshot cru (direcionais não têm alias, então normalized == snapshot
    # para essas chaves) para invalidar capturas finas na origem.
    errors.extend(directional_capture_errors(snapshot))
    source_times, source_errors = _feature_source_timestamps(
        normalized,
        source_snapshot=source_snapshot,
        feature_metadata=feature_metadata,
    )
    source_at = max(source_times.values()) if source_times else None
    if source_at is None:
        source_errors.append("missing_feature_source_at")
    errors.extend(source_errors)
    errors.extend(temporal_contract_errors(
        feature_source_at=source_at,
        features_captured_at=captured_at,
        decision_created_at=decision_created_at,
        entry_at=entry_at,
        label_resolved_at=None,
    ))
    return NativeFeatureCapture(
        snapshot=normalized,
        captured_at=captured_at,
        source_at=source_at,
        source_times={
            name: timestamp.isoformat()
            for name, timestamp in sorted(source_times.items())
        },
        snapshot_hash=snapshot_hash(normalized),
        feature_extractor_version=FEATURE_EXTRACTOR_VERSION,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        capture_contract_version=CAPTURE_CONTRACT_VERSION,
        errors=tuple(errors),
    )


def temporal_contract_errors(
    *,
    feature_source_at: datetime | None,
    features_captured_at: datetime | None,
    decision_created_at: datetime | None,
    entry_at: datetime | None,
    label_resolved_at: datetime | None,
) -> list[str]:
    errors: list[str] = []
    ordered = (
        (feature_source_at, features_captured_at, "feature_source_after_capture"),
        (feature_source_at, decision_created_at, "feature_source_after_decision"),
        (feature_source_at, entry_at, "feature_source_after_entry"),
        (decision_created_at, entry_at, "decision_after_entry"),
    )
    for earlier, later, reason in ordered:
        if earlier is not None and later is not None and earlier > later:
            errors.append(reason)
    if label_resolved_at is not None and decision_created_at is not None:
        if label_resolved_at <= decision_created_at:
            errors.append("label_not_after_decision")
    return errors


def coverage(snapshot: Mapping[str, Any]) -> float:
    present = sum(snapshot.get(name) is not None for name in REGISTRY)
    return present / len(REGISTRY)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
