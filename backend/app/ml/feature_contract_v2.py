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
CAPTURE_CONTRACT_VERSION = "point-in-time-v1"


@dataclass(frozen=True)
class NativeFeatureCapture:
    snapshot: dict[str, Any]
    captured_at: datetime
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


def capture_native_snapshot(snapshot: Mapping[str, Any]) -> NativeFeatureCapture:
    captured_at = utcnow()
    normalized, errors = normalize_snapshot(snapshot)
    # P0-B: bloco direcional é requisito de elegibilidade. Checado sobre o
    # snapshot cru (direcionais não têm alias, então normalized == snapshot
    # para essas chaves) para invalidar capturas finas na origem.
    errors.extend(directional_capture_errors(snapshot))
    return NativeFeatureCapture(
        snapshot=normalized,
        captured_at=captured_at,
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
        (feature_source_at, features_captured_at, "feature_after_capture"),
        (features_captured_at, decision_created_at, "features_after_decision"),
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
