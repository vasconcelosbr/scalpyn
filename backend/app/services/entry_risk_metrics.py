"""Low-cardinality Prometheus metrics for entry-risk observation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram

    _COMPUTATION = Counter(
        "entry_risk_score_computation_total",
        "Entry-risk capture computations by terminal status.",
        ("status", "profile_family", "regime", "formula_version"),
    )
    _QUALITY = Counter(
        "entry_risk_data_quality_total",
        "Entry-risk missing, stale, timestamp, hash and reconstruction events.",
        ("reason", "profile_family", "regime", "formula_version"),
    )
    _LATENCY = Histogram(
        "entry_risk_latency_ms",
        "Entry-risk capture latency in milliseconds.",
        ("status", "formula_version"),
    )
    _NULL_RATE = Gauge(
        "entry_risk_null_rate",
        "Observed aggregate-score null rate.",
        ("score_name", "profile_family", "regime", "formula_version"),
    )
    _DRIFT = Gauge(
        "entry_risk_distribution_drift",
        "Distribution drift reported by the scheduled validation job.",
        ("score_name", "profile_family", "regime", "formula_version"),
    )
    _ARTIFACT = Counter(
        "entry_risk_artifact_total",
        "Presence and validity of bounded entry-risk capture artifacts.",
        ("artifact", "state", "formula_version"),
    )
except Exception as exc:  # pragma: no cover - optional dependency
    logger.debug("prometheus_client unavailable: %s", exc)
    _COMPUTATION = _QUALITY = _LATENCY = _NULL_RATE = _DRIFT = _ARTIFACT = None


def record_capture(contract: dict, latency_ms: float) -> None:
    status_block = contract.get("contract_status") or {}
    context = contract.get("context") or {}
    legacy = contract.get("legacy") or {}
    status = str(status_block.get("status") or "ERROR")
    family = str(context.get("profile_family") or "UNKNOWN")
    regime_payload = context.get("regime") or {}
    regime = str(regime_payload.get("regime") or "UNKNOWN")
    formula = str(legacy.get("formula_version") or "NONE")
    try:
        if _COMPUTATION is not None:
            _COMPUTATION.labels(status, family, regime, formula).inc()
        if _LATENCY is not None:
            _LATENCY.labels(status, formula).observe(latency_ms)
        if _QUALITY is not None:
            for reason in status_block.get("reason_codes") or []:
                _QUALITY.labels(str(reason), family, regime, formula).inc()
        if _NULL_RATE is not None:
            _NULL_RATE.labels("momentum_intensity", family, regime, formula).set(1.0)
            _NULL_RATE.labels("exhaustion_risk", family, regime, formula).set(1.0)
        if _ARTIFACT is not None:
            window = contract.get("candle_window") or {}
            _ARTIFACT.labels(
                "candle_window_hash",
                "present" if window.get("candle_window_hash") else "missing",
                formula,
            ).inc()
            _ARTIFACT.labels(
                "legacy_reconstruction",
                "valid" if status_block.get("reconstructible") else "invalid",
                formula,
            ).inc()
            _ARTIFACT.labels(
                "capture_timestamp",
                "present" if contract.get("captured_at") else "missing",
                formula,
            ).inc()
    except Exception as exc:  # pragma: no cover - telemetry cannot break capture
        logger.debug("entry-risk metrics failed: %s", exc)


def set_distribution_drift(
    score_name: str,
    value: float,
    *,
    profile_family: str = "ALL",
    regime: str = "ALL",
    formula_version: str = "NONE",
) -> None:
    try:
        if _DRIFT is not None:
            _DRIFT.labels(score_name, profile_family, regime, formula_version).set(value)
    except Exception as exc:  # pragma: no cover
        logger.debug("entry-risk drift metric failed: %s", exc)
