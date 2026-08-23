"""Durable, idempotent storage for observational L3 gate v2 evaluations."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import text

from .l3_gate_v2_metrics import observe_gate_capture

logger = logging.getLogger(__name__)

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

_UPSERT = text("""
    INSERT INTO l3_gate_v2_evaluations (
        evaluation_envelope_hash, user_id, watchlist_id,
        profile_id, profile_name, symbol, timeframe, evaluated_at,
        legacy_decision, shadow_decision, decision_drift,
        operational_effect, payload
    ) VALUES (
        :evaluation_envelope_hash, CAST(:user_id AS UUID),
        CAST(:watchlist_id AS UUID), CAST(:profile_id AS UUID),
        :profile_name, :symbol, :timeframe, :evaluated_at,
        :legacy_decision, :shadow_decision, :decision_drift,
        :operational_effect, CAST(:payload AS JSONB)
    )
    ON CONFLICT (evaluation_envelope_hash) DO UPDATE
       SET last_seen_at = now(),
           capture_attempts = l3_gate_v2_evaluations.capture_attempts + 1
    RETURNING id, capture_attempts
""")

_LINK_DECISION = text("""
    UPDATE l3_gate_v2_evaluations
       SET decision_id = :decision_id,
           last_seen_at = now()
     WHERE evaluation_envelope_hash = :evaluation_envelope_hash
       AND decision_id IS NULL
""")

_LINK_SHADOW = text("""
    UPDATE l3_gate_v2_evaluations
       SET shadow_trade_id = CAST(:shadow_trade_id AS UUID),
           last_seen_at = now()
     WHERE evaluation_envelope_hash = :evaluation_envelope_hash
       AND shadow_trade_id IS NULL
""")


def _parse_evaluated_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    raise ValueError("evaluated_at_missing_or_invalid")


def _capture_row(
    decision: dict[str, Any],
    *,
    user_id: Any,
    watchlist_id: Any,
    profile_id: Any,
    profile_name: str | None,
) -> dict[str, Any]:
    payload = decision.get("gate_evaluation_v2") or (
        (decision.get("metrics") or {}).get("l3_gate_v2")
    )
    if not isinstance(payload, dict):
        raise ValueError("gate_v2_payload_missing")
    envelope_hash = payload.get("evaluation_envelope_hash")
    if not isinstance(envelope_hash, str) or not _HASH_RE.fullmatch(envelope_hash):
        raise ValueError("evaluation_envelope_hash_invalid")
    if payload.get("contract_version") != "l3_gate_v2":
        raise ValueError("contract_version_invalid")
    operational_effect = payload.get("operational_effect")
    if not isinstance(operational_effect, bool):
        raise ValueError("operational_effect_must_be_boolean")
    if operational_effect and (
        payload.get("promotion_status") != "OPERATIONAL"
        or payload.get("operational_decision") not in {"ALLOW", "BLOCK"}
    ):
        raise ValueError("operational_promotion_metadata_invalid")
    if not operational_effect and payload.get("promotion_status") == "OPERATIONAL":
        raise ValueError("operational_promotion_metadata_invalid")

    return {
        "evaluation_envelope_hash": envelope_hash,
        "user_id": str(user_id) if user_id else None,
        "watchlist_id": str(watchlist_id) if watchlist_id else None,
        "profile_id": str(profile_id) if profile_id else None,
        "profile_name": profile_name,
        "symbol": str(decision.get("symbol") or ""),
        "timeframe": decision.get("timeframe"),
        "evaluated_at": _parse_evaluated_at(payload.get("evaluated_at")),
        "legacy_decision": str(payload.get("legacy_decision") or "UNKNOWN"),
        "shadow_decision": str(payload.get("shadow_decision") or "UNKNOWN"),
        "decision_drift": bool(payload.get("decision_drift", False)),
        "operational_effect": operational_effect,
        "payload": json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str),
    }


async def persist_gate_evaluations(
    db,
    decisions: Iterable[dict[str, Any]],
    *,
    user_id: Any,
    watchlist_id: Any,
    profile_id: Any,
    profile_name: str | None,
) -> dict[str, int]:
    """Persist every computed v2 evaluation before decision-log filtering."""

    decision_list = list(decisions)
    report = {
        "expected": len(decision_list),
        "captured": 0,
        "inserted": 0,
        "replayed": 0,
        "invalid": 0,
    }
    for decision in sorted(decision_list, key=lambda item: item.get("symbol") or ""):
        try:
            row = _capture_row(
                decision,
                user_id=user_id,
                watchlist_id=watchlist_id,
                profile_id=profile_id,
                profile_name=profile_name,
            )
        except (TypeError, ValueError) as exc:
            report["invalid"] += 1
            observe_gate_capture("invalid")
            logger.error(
                "[L3_GATE_V2_CAPTURE] status=INVALID symbol=%s profile_id=%s reason=%s",
                decision.get("symbol"), profile_id, exc,
            )
            continue

        result = await db.execute(_UPSERT, row)
        persisted = result.fetchone()
        report["captured"] += 1
        if persisted is not None and int(persisted[1] or 1) > 1:
            report["replayed"] += 1
            observe_gate_capture("replayed")
        else:
            report["inserted"] += 1
            observe_gate_capture("inserted")

    if report["captured"] != report["expected"]:
        observe_gate_capture("count_mismatch")
        logger.error(
            "[L3_GATE_V2_CAPTURE] status=COUNT_MISMATCH expected=%d captured=%d "
            "invalid=%d watchlist_id=%s profile_id=%s",
            report["expected"], report["captured"], report["invalid"],
            watchlist_id, profile_id,
        )
    else:
        logger.info(
            "[L3_GATE_V2_CAPTURE] status=OK expected=%d captured=%d inserted=%d "
            "replayed=%d watchlist_id=%s profile_id=%s",
            report["expected"], report["captured"], report["inserted"],
            report["replayed"], watchlist_id, profile_id,
        )
    return report


async def link_decision_evaluation(db, *, decision_id: int, payload: dict[str, Any]) -> None:
    gate = (payload.get("metrics") or {}).get("l3_gate_v2") or {}
    envelope_hash = gate.get("evaluation_envelope_hash")
    if isinstance(envelope_hash, str) and _HASH_RE.fullmatch(envelope_hash):
        await db.execute(
            _LINK_DECISION,
            {"decision_id": decision_id, "evaluation_envelope_hash": envelope_hash},
        )
        observe_gate_capture("decision_linked")


async def link_shadow_evaluation(
    db, *, shadow_trade_id: Any, gate_payload: dict[str, Any]
) -> None:
    envelope_hash = gate_payload.get("evaluation_envelope_hash")
    if isinstance(envelope_hash, str) and _HASH_RE.fullmatch(envelope_hash):
        await db.execute(
            _LINK_SHADOW,
            {
                "shadow_trade_id": str(shadow_trade_id),
                "evaluation_envelope_hash": envelope_hash,
            },
        )
        observe_gate_capture("shadow_linked")
