"""Low-cardinality observability for the L3 authorization v3 shadow rollout."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge  # type: ignore[import-untyped]

    _EVALUATIONS = Counter(
        "l3_authorization_v3_evaluations_total",
        "L3 authorization v3 evaluations",
        ["authorization_status", "legacy_decision", "contract_decision", "drift"],
    )
    _REJECTIONS = Counter(
        "l3_authorization_v3_contract_reject_total",
        "L3 authorization v3 contract rejection reasons",
        ["reason"],
    )
    _ML = Counter(
        "l3_ml_advisory_total",
        "L3 ML advisory states without operational effect",
        ["status"],
    )
    _OUTBOX = Counter(
        "l3_authorization_outbox_total",
        "L3 authorization outbox processing outcomes",
        ["outcome"],
    )
    _REQUIRED_MISSING = Counter(
        "l3_required_feature_missing_total", "Required L3 features not available"
    )
    _SOURCE_MISMATCH = Counter(
        "l3_source_mismatch_total", "L3 source identity mismatches"
    )
    _TIMEFRAME_MISMATCH = Counter(
        "l3_timeframe_mismatch_total", "L3 timeframe identity mismatches"
    )
    _WINDOW_MISMATCH = Counter(
        "l3_window_mismatch_total", "L3 window identity mismatches"
    )
    _REQUIRED_STALE = Counter(
        "l3_required_feature_stale_total", "Required L3 features stale or TTL expired"
    )
    _SECTION_HASH_MISMATCH = Counter(
        "l3_section_hash_mismatch_total", "L3 section hash mismatches"
    )
    _OUTBOX_PENDING = Gauge(
        "l3_outbox_pending_total", "Current pending or retry L3 outbox events"
    )
    _OUTBOX_FAILED = Counter(
        "l3_outbox_processing_failed_total", "Failed L3 outbox processing attempts"
    )
except Exception as exc:  # pragma: no cover - optional locally
    _EVALUATIONS = _REJECTIONS = _ML = _OUTBOX = None
    _REQUIRED_MISSING = _SOURCE_MISMATCH = _TIMEFRAME_MISMATCH = None
    _WINDOW_MISMATCH = _REQUIRED_STALE = _SECTION_HASH_MISMATCH = None
    _OUTBOX_PENDING = _OUTBOX_FAILED = None
    logger.debug("prometheus_client unavailable: %s", exc)


def _condition_results(contract: dict):
    sections = contract.get("sections") or {}
    for name, section in sections.items():
        if name == "block_rules":
            for block in section.get("blocks") or []:
                yield from block.get("conditions") or []
        else:
            yield from section.get("conditions") or []


def observe_authorization_contract(contract: dict) -> None:
    if _EVALUATIONS is None:
        return
    try:
        _EVALUATIONS.labels(
            authorization_status=str(contract.get("authorization_status") or "UNKNOWN"),
            legacy_decision=str(contract.get("legacy_technical_decision") or "UNKNOWN"),
            contract_decision=str(contract.get("contract_technical_decision") or "UNKNOWN"),
            drift="true" if contract.get("decision_drift") else "false",
        ).inc()
        if _REJECTIONS is not None:
            for condition in _condition_results(contract):
                if condition.get("status") != "CONTRACT_REJECT":
                    continue
                for reason in condition.get("reason_codes") or ["UNKNOWN"]:
                    _REJECTIONS.labels(reason=str(reason)).inc()
                    if reason == "FEATURE_IDENTITY_NOT_AVAILABLE" and condition.get("required"):
                        _REQUIRED_MISSING.inc()
                    elif reason == "SOURCE_MISMATCH":
                        _SOURCE_MISMATCH.inc()
                    elif reason == "TIMEFRAME_MISMATCH":
                        _TIMEFRAME_MISMATCH.inc()
                    elif reason == "WINDOW_MISMATCH":
                        _WINDOW_MISMATCH.inc()
                    elif reason in {"FEATURE_STALE", "FEATURE_TTL_EXPIRED"} and condition.get("required"):
                        _REQUIRED_STALE.inc()
        if _ML is not None:
            _ML.labels(
                status=str((contract.get("ml_advisory") or {}).get("ml_status") or "UNKNOWN")
            ).inc()
    except Exception:
        logger.debug("failed to emit L3 authorization v3 metrics", exc_info=True)


def observe_outbox(outcome: str) -> None:
    if _OUTBOX is None:
        return
    allowed = {"processed", "retried", "skipped"}
    normalized = outcome if outcome in allowed else "retried"
    try:
        _OUTBOX.labels(outcome=normalized).inc()
        if normalized == "retried" and _OUTBOX_FAILED is not None:
            _OUTBOX_FAILED.inc()
    except Exception:
        logger.debug("failed to emit L3 outbox metric", exc_info=True)


def set_outbox_pending(count: int) -> None:
    if _OUTBOX_PENDING is not None:
        _OUTBOX_PENDING.set(max(int(count), 0))


def observe_section_hash_mismatch() -> None:
    if _SECTION_HASH_MISMATCH is not None:
        _SECTION_HASH_MISMATCH.inc()
