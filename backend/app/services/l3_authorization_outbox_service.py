"""Transactional outbox consumer for L3 authorization contract v3."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from ..models.backoffice import DecisionLog, L3AuthorizationOutbox
from .l3_authorization_contract_v3 import canonical_hash

logger = logging.getLogger(__name__)

DIRECT_EVENT = "CREATE_SHADOW_IF_ALLOWED"
CONSOLIDATION_EVENT = "CONSOLIDATE_SHADOW_IF_ALLOWED"


def _contract(decision: DecisionLog, event: L3AuthorizationOutbox) -> dict:
    contract = (decision.metrics or {}).get("l3_authorization_contract_v3")
    if not isinstance(contract, dict):
        raise ValueError("AUTHORIZATION_CONTRACT_NOT_FOUND")
    stored_hash = contract.get("authorization_contract_hash")
    if stored_hash != event.authorization_contract_hash:
        raise ValueError("AUTHORIZATION_CONTRACT_HASH_MISMATCH")
    hash_body = dict(contract)
    hash_body.pop("authorization_contract_hash", None)
    if canonical_hash(hash_body) != stored_hash:
        raise ValueError("AUTHORIZATION_CONTRACT_CONTENT_HASH_MISMATCH")
    if contract.get("mode") not in {"SHADOW", "ENFORCE"}:
        raise ValueError("AUTHORIZATION_CONTRACT_MODE_INVALID")
    if contract.get("final_decision") != contract.get("technical_decision"):
        raise ValueError("ML_OPERATIONAL_EFFECT_FORBIDDEN")
    return contract


def _profile_lineage(contract: dict) -> dict:
    return contract.get("profile_lineage") or contract.get("lineage") or {}


def _watchlist_lineage(contract: dict) -> dict:
    explicit = contract.get("watchlist_lineage")
    if isinstance(explicit, dict):
        return explicit
    lineage = contract.get("lineage") or {}
    return {
        "status": lineage.get("watchlist_status"),
        "watchlist_id": lineage.get("watchlist_id"),
        "watchlist_name": lineage.get("watchlist_name"),
        "watchlist_level": lineage.get("watchlist_level"),
        "source_watchlist_id": lineage.get("source_watchlist_id"),
    }


def _validate_lineage(
    decision: DecisionLog, event: L3AuthorizationOutbox, contract: dict
) -> None:
    profile = _profile_lineage(contract)
    watchlist = _watchlist_lineage(contract)
    payload = event.payload or {}
    if not profile.get("profile_id") or not profile.get("profile_version"):
        raise ValueError("PROFILE_LINEAGE_MISSING")
    if not profile.get("rules_snapshot"):
        raise ValueError("RULES_SNAPSHOT_MISSING")
    if decision.profile_id is not None and str(decision.profile_id) != str(
        profile.get("profile_id")
    ):
        raise ValueError("PROFILE_LINEAGE_DIVERGENT")
    if payload.get("user_id") and str(payload.get("user_id")) != str(decision.user_id):
        raise ValueError("CALLER_USER_LINEAGE_DIVERGENT")
    if watchlist.get("status") not in {"RESOLVED", "NOT_APPLICABLE"}:
        raise ValueError("WATCHLIST_LINEAGE_INVALID")
    if watchlist.get("status") == "RESOLVED" and not watchlist.get("watchlist_id"):
        raise ValueError("WATCHLIST_LINEAGE_MISSING")


def _authorized_for_shadow(decision: DecisionLog, contract: dict) -> bool:
    if contract.get("valid") is not True:
        return False
    if contract.get("authorization_status") != "ALLOW":
        return False
    if contract.get("contract_technical_decision") != "ALLOW":
        return False
    if contract.get("final_decision") != "ALLOW":
        return False
    # SHADOW never changes the current deterministic decision; it only permits
    # a shadow when both authorities independently agree on ALLOW.
    if contract.get("mode") == "SHADOW" and decision.decision != "ALLOW":
        return False
    return True


def _not_created_result(contract: dict, *, required: bool) -> str:
    if contract.get("valid") is not True or contract.get("authorization_status") == "CONTRACT_REJECT":
        return "CONTRACT_REJECT"
    return "NO_SHADOW_REQUIRED"


def _direct_processing_result(
    contract: dict,
    *,
    required: bool,
    trade_id: Any = None,
    active_exists: bool = False,
) -> str:
    if trade_id is not None:
        return "CREATED_OR_RECONCILED"
    if active_exists:
        return "SUPPRESSED/ACTIVE_TRADE_ALREADY_EXISTS"
    return _not_created_result(contract, required=required)


def _consolidation_processing_result(result: Any, candidate: Any) -> str:
    if result.decision == "SUPPRESSED":
        return f"SUPPRESSED/{result.reason_code}"
    if (
        result.decision == "CREATED"
        and str(candidate.profile_id or "") == str(result.winner_profile_id or "")
    ):
        return "CREATED_OR_RECONCILED"
    return "SUPPRESSED/SAME_SYMBOL_LOWER_PRIORITY"


def _lineage(event: L3AuthorizationOutbox, contract: dict):
    from ..schemas.watchlist_lineage_context import WatchlistLineageContext

    profile = _profile_lineage(contract)
    watchlist = _watchlist_lineage(contract)
    raw_version = profile.get("profile_version")
    profile_version = raw_version
    if isinstance(raw_version, str):
        try:
            profile_version = datetime.fromisoformat(raw_version.replace("Z", "+00:00"))
        except ValueError:
            profile_version = None
    ml = (event.payload or {}).get("ml_score") or {}
    lineage_reason_codes: list[str] = []
    for code in [
        *(contract.get("reason_codes") or []),
        *(ml.get("reason_codes") or []),
    ]:
        if code not in lineage_reason_codes:
            lineage_reason_codes.append(code)
    if contract.get("authorization_status") == "ALLOW" and not lineage_reason_codes:
        lineage_reason_codes.append("L3_AUTHORIZATION_ALLOW")
    return WatchlistLineageContext(
        watchlist_id=watchlist.get("watchlist_id"),
        watchlist_name=watchlist.get("watchlist_name"),
        watchlist_level=watchlist.get("watchlist_level"),
        source_watchlist_id=watchlist.get("source_watchlist_id"),
        profile_id=profile.get("profile_id"),
        profile_name=profile.get("profile_name"),
        profile_version=profile_version,
        rules_snapshot=profile.get("rules_snapshot") or {},
        lineage_confidence=(
            "EXACT" if watchlist.get("status") == "RESOLVED"
            else "NOT_APPLICABLE"
        ),
        lineage_source="l3_authorization_outbox_v3",
        ml_model_id=ml.get("model_id"),
        ml_probability=ml.get("probability"),
        model_lane=ml.get("model_lane"),
        model_version=ml.get("model_version"),
        threshold_used=ml.get("threshold"),
        score_status=ml.get("score_status"),
        gate_action=ml.get("gate_action"),
        reason_codes=lineage_reason_codes,
        ml_gate_enabled=False,
    )


async def _mark_retry(event_ids: list[Any], error: Exception) -> None:
    from ..database import CeleryAsyncSessionLocal

    if not event_ids:
        return
    async with CeleryAsyncSessionLocal() as db:
        async with db.begin():
            events = list((await db.execute(
                select(L3AuthorizationOutbox)
                .where(L3AuthorizationOutbox.id.in_(event_ids))
                .with_for_update()
            )).scalars())
            for event in events:
                if event.status == "PROCESSED":
                    continue
                event.status = "RETRY"
                event.attempt_count = int(event.attempt_count or 0) + 1
                event.last_error = f"{type(error).__name__}:{str(error)[:1000]}"
                event.available_at = datetime.now(timezone.utc)


async def _mark_processed(event_ids: list[Any], result: str) -> None:
    from ..database import CeleryAsyncSessionLocal

    if not event_ids:
        return
    async with CeleryAsyncSessionLocal() as db:
        async with db.begin():
            events = list((await db.execute(
                select(L3AuthorizationOutbox)
                .where(L3AuthorizationOutbox.id.in_(event_ids))
                .with_for_update()
            )).scalars())
            for event in events:
                if event.status == "PROCESSED":
                    continue
                payload = dict(event.payload or {})
                payload["processing_result"] = result
                event.payload = payload
                event.status = "PROCESSED"
                event.processed_at = datetime.now(timezone.utc)
                event.last_error = None


async def _process_direct(event_id: Any) -> str:
    from ..database import CeleryAsyncSessionLocal
    from .shadow_trade_service import _create_from_decision, load_shadow_creation_config

    async with CeleryAsyncSessionLocal() as db:
        async with db.begin():
            event = (
                await db.execute(
                    select(L3AuthorizationOutbox)
                    .where(L3AuthorizationOutbox.id == event_id)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if event is None or event.status not in {"PENDING", "RETRY"}:
                return "SKIPPED"
            if event.event_type != DIRECT_EVENT:
                raise ValueError("OUTBOX_EVENT_TYPE_INVALID")
            event.attempt_count = int(event.attempt_count or 0) + 1
            decision = await db.get(DecisionLog, event.decision_id)
            if decision is None:
                raise ValueError("DECISION_NOT_FOUND")
            contract = _contract(decision, event)
            required = bool((event.payload or {}).get("shadow_creation_required", False))
            processing_result = _direct_processing_result(
                contract, required=required
            )
            if required and _authorized_for_shadow(decision, contract):
                _validate_lineage(decision, event, contract)
                config = await load_shadow_creation_config(decision.user_id)
                trade_id = await _create_from_decision(
                    db,
                    decision,
                    "L3_AUTHORIZATION_OUTBOX_V3",
                    config,
                    lineage=_lineage(event, contract),
                )
                if trade_id is not None:
                    processing_result = "CREATED_OR_RECONCILED"
                else:
                    from .l3_trade_consolidation import find_active_l3_shadow

                    active = await find_active_l3_shadow(
                        db,
                        user_id=decision.user_id,
                        symbol=decision.symbol,
                        direction=(decision.direction or "SPOT").upper(),
                    )
                    processing_result = _direct_processing_result(
                        contract,
                        required=required,
                        active_exists=active is not None,
                    )
            payload = dict(event.payload or {})
            payload["processing_result"] = processing_result
            event.payload = payload
            event.status = "PROCESSED"
            event.processed_at = datetime.now(timezone.utc)
            event.last_error = None
            return payload["processing_result"]


async def _consolidation_rows(scan_run_id: str) -> list[tuple[L3AuthorizationOutbox, DecisionLog]]:
    from ..database import CeleryAsyncSessionLocal

    async with CeleryAsyncSessionLocal() as db:
        result = await db.execute(
            select(L3AuthorizationOutbox, DecisionLog)
            .join(DecisionLog, DecisionLog.id == L3AuthorizationOutbox.decision_id)
            .where(
                L3AuthorizationOutbox.event_type == CONSOLIDATION_EVENT,
                L3AuthorizationOutbox.status.in_(["PENDING", "RETRY"]),
                L3AuthorizationOutbox.payload["scan_run_id"].astext == scan_run_id,
            )
            .order_by(L3AuthorizationOutbox.created_at.asc())
        )
        return list(result.all())


async def _process_consolidation(scan_run_id: str) -> tuple[int, str]:
    from .l3_trade_consolidation import (
        candidate_from_decision,
        consolidate_l3_candidates,
    )

    rows = await _consolidation_rows(scan_run_id)
    if not rows:
        return 0, "SKIPPED"
    candidates_by_policy: dict[tuple[str, str], list[Any]] = {}
    candidate_events: dict[str, list[tuple[Any, Any]]] = {}
    immediate_results: dict[Any, str] = {}
    for event, decision in rows:
        contract = _contract(decision, event)
        payload = event.payload or {}
        if not payload.get("consolidation_required"):
            immediate_results[event.id] = "NO_SHADOW_REQUIRED"
            continue
        if not _authorized_for_shadow(decision, contract):
            immediate_results[event.id] = _not_created_result(
                contract, required=True
            )
            continue
        _validate_lineage(decision, event, contract)
        lineage = _lineage(event, contract)
        candidate = candidate_from_decision(
            user_id=decision.user_id,
            decision_id=decision.id,
            decision={
                "symbol": decision.symbol,
                "direction": decision.direction,
                "timeframe": decision.timeframe,
                "score": decision.score,
                "created_at": decision.created_at,
                "metrics": decision.metrics or {},
            },
            buy_threshold=payload.get("buy_threshold"),
            strong_buy_threshold=payload.get("strong_buy_threshold"),
            profile_id=lineage.profile_id,
            profile_name=lineage.profile_name,
            profile_version=lineage.profile_version,
            rules_snapshot=lineage.rules_snapshot,
            watchlist_id=lineage.watchlist_id,
            watchlist_name=lineage.watchlist_name,
            watchlist_level=lineage.watchlist_level,
            source_watchlist_id=lineage.source_watchlist_id,
            ml_score=payload.get("ml_score") or {},
        )
        rule_version = str(payload.get("consolidation_rule_version") or "")
        candidates_by_policy.setdefault(
            (str(decision.user_id), rule_version), []
        ).append(candidate)
        candidate_events.setdefault(candidate.event_id, []).append((event.id, candidate))
    result_count = 0
    result_by_event: dict[str, Any] = {}
    for (_user_id, rule_version), candidates in sorted(candidates_by_policy.items()):
        results = await consolidate_l3_candidates(
            candidates,
            scan_run_id=scan_run_id,
            rule_version=rule_version,
        )
        result_count += len(results)
        for result in results:
            result_by_event[result.event_id] = result
    for event_id, result in immediate_results.items():
        await _mark_processed([event_id], result)
    for consolidation_event_id, event_candidates in candidate_events.items():
        result = result_by_event.get(consolidation_event_id)
        if result is None or result.decision == "ERROR":
            raise RuntimeError(
                f"L3_CONSOLIDATION_RESULT_INVALID:{consolidation_event_id}"
            )
        for event_id, candidate in event_candidates:
            processing_result = _consolidation_processing_result(result, candidate)
            await _mark_processed([event_id], processing_result)
    return len(rows), f"CONSOLIDATED:{result_count}"


async def process_l3_authorization_outbox(
    batch_size: int = 50,
    *,
    scan_run_id: str | None = None,
) -> dict[str, int]:
    """Process direct events and complete consolidation batches.

    ``scan_run_id`` is used by the scanner only after every watchlist decision
    in that run has committed. Beat omits it and retries any available batch.
    """
    from ..database import CeleryAsyncSessionLocal
    from .l3_authorization_metrics import observe_outbox, set_outbox_pending

    async with CeleryAsyncSessionLocal() as db:
        pending_total = int((await db.execute(
            select(func.count()).select_from(L3AuthorizationOutbox).where(
                L3AuthorizationOutbox.status.in_(["PENDING", "RETRY"])
            )
        )).scalar_one() or 0)
        set_outbox_pending(pending_total)
        query = select(
            L3AuthorizationOutbox.id,
            L3AuthorizationOutbox.event_type,
            L3AuthorizationOutbox.payload,
        ).where(
            L3AuthorizationOutbox.status.in_(["PENDING", "RETRY"]),
        )
        if scan_run_id is None:
            query = query.where(
                L3AuthorizationOutbox.available_at <= datetime.now(timezone.utc)
            )
        else:
            query = query.where(
                L3AuthorizationOutbox.payload["scan_run_id"].astext == scan_run_id
            )
        selected = list((await db.execute(
            query.order_by(L3AuthorizationOutbox.created_at.asc()).limit(batch_size)
        )).all())

    direct_ids = [row.id for row in selected if row.event_type == DIRECT_EVENT]
    scan_ids = sorted({
        str((row.payload or {}).get("scan_run_id"))
        for row in selected
        if row.event_type == CONSOLIDATION_EVENT
        and (row.payload or {}).get("scan_run_id")
    })
    counts = {
        "selected": len(selected), "processed": 0, "retried": 0,
        "skipped": 0, "consolidation_batches": 0,
    }
    for event_id in direct_ids:
        try:
            status = await _process_direct(event_id)
            if status == "SKIPPED":
                counts["skipped"] += 1
                observe_outbox("skipped")
            else:
                counts["processed"] += 1
                observe_outbox("processed")
        except Exception as exc:
            counts["retried"] += 1
            observe_outbox("retried")
            await _mark_retry([event_id], exc)
            logger.exception("[L3_OUTBOX_V3] retry event_id=%s", event_id)
    for current_scan_id in scan_ids:
        event_ids: list[Any] = []
        try:
            rows = await _consolidation_rows(current_scan_id)
            event_ids = [event.id for event, _decision in rows]
            processed, status = await _process_consolidation(current_scan_id)
            counts["processed"] += processed
            counts["consolidation_batches"] += 1
            observe_outbox("processed")
            logger.info(
                "[L3_OUTBOX_V3] scan_run_id=%s status=%s events=%d",
                current_scan_id, status, processed,
            )
        except Exception as exc:
            counts["retried"] += len(event_ids) or 1
            observe_outbox("retried")
            await _mark_retry(event_ids, exc)
            logger.exception(
                "[L3_OUTBOX_V3] consolidation retry scan_run_id=%s",
                current_scan_id,
            )
    return counts
