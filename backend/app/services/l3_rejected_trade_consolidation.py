"""Canonical consolidation for diagnostic L3 rejected Shadows.

The approved and rejected lanes deliberately remain independent.  This module
collects every BLOCK/filter rejection in one scanner run, ranks each market
event with the same v1 ordering used by approved L3 candidates, and persists at
most one ``source='L3_REJECTED'`` Shadow owner.  Rejected rows are observational
only and never feed authorization decisions.
"""

from __future__ import annotations

import hashlib
import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select, text

from ..models.backoffice import DecisionLog
from ..models.config_profile import ConfigProfile
from ..schemas.watchlist_lineage_context import WatchlistLineageContext
from .l3_trade_consolidation import (
    ACTIVE_SHADOW_STATUSES,
    RULE_VERSION_V1,
    SUPPORTED_RULE_VERSIONS,
    ConsolidationResult,
    _as_float,
    _utc,
    acquire_consolidation_lock,
    build_consolidation_event_id,
    candle_open_for_timeframe,
    find_active_shadow_for_source,
    rank_candidates,
)

logger = logging.getLogger(__name__)

SOURCE = "L3_REJECTED"
SUPPRESSION_EVENT_TYPE = "PROFILE_CONSOLIDATION_REJECTED"
REASON_LOWER_PRIORITY = "SAME_SYMBOL_LOWER_PRIORITY"
REASON_ACTIVE_REJECTED = "ACTIVE_REJECTED_SHADOW_ALREADY_EXISTS"
REASON_CONCURRENT_REJECTED = "CONCURRENT_REJECTED_SHADOW_CREATED"
REASON_RATE_LIMIT = "REJECTED_CAPTURE_RATE_LIMIT"

SELECTION_RULE = [
    "normalized_score_margin_desc",
    "decision_score_desc",
    "market_structure_score_desc",
    "momentum_score_desc",
    "liquidity_score_desc",
    "signal_score_desc",
    "profile_name_asc",
    "profile_id_asc",
]


@dataclass(frozen=True)
class RejectedL3Candidate:
    user_id: Any
    symbol: str
    direction: str
    timeframe: str
    candle_open_timestamp: datetime
    observed_at: datetime
    profile_id: Optional[Any]
    profile_name: Optional[str]
    profile_version: Optional[datetime]
    profile_version_id: Optional[Any]
    decision_score: float
    buy_threshold: float
    strong_buy_threshold: float
    decision: dict[str, Any]
    rules_snapshot: Optional[dict[str, Any]] = None
    market_structure_score: Optional[float] = None
    momentum_score: Optional[float] = None
    liquidity_score: Optional[float] = None
    signal_score: Optional[float] = None
    watchlist_id: Optional[str] = None
    watchlist_name: Optional[str] = None
    watchlist_level: Optional[str] = None
    source_watchlist_id: Optional[str] = None
    rejection_stage: str = "L3"
    rule_version: str = RULE_VERSION_V1

    @property
    def normalized_score_margin(self) -> float:
        denominator = max(self.strong_buy_threshold - self.buy_threshold, 1.0)
        margin = (self.decision_score - self.buy_threshold) / denominator
        return min(max(margin, 0.0), 1.0)

    @property
    def event_id(self) -> str:
        return build_consolidation_event_id(
            symbol=self.symbol,
            direction=self.direction,
            timeframe=self.timeframe,
            candle_open_timestamp=self.candle_open_timestamp,
            lane=SOURCE,
        )


def rejected_candidate_from_decision(
    *,
    user_id: Any,
    decision: dict[str, Any],
    observed_at: datetime,
    buy_threshold: float,
    strong_buy_threshold: float,
    profile_id: Optional[Any],
    profile_name: Optional[str],
    profile_version: Optional[datetime],
    profile_version_id: Optional[Any],
    rules_snapshot: Optional[dict[str, Any]],
    watchlist_id: Optional[str],
    watchlist_name: Optional[str],
    watchlist_level: Optional[str],
    source_watchlist_id: Optional[str],
    rule_version: str = RULE_VERSION_V1,
) -> RejectedL3Candidate:
    metrics = decision.get("metrics") or {}
    components = metrics.get("score_components") or {}
    timeframe = str(
        decision.get("timeframe")
        or (rules_snapshot or {}).get("default_timeframe")
        or "5m"
    )
    symbol = str(decision.get("symbol") or "").upper()
    if not symbol:
        raise ValueError("rejected_candidate_symbol_missing")
    direction = str(decision.get("direction") or "SPOT").upper()
    stage = (
        "PROFILE_FILTER"
        if metrics.get("source") == "l3_filter_rejected"
        else "ENTRY_TRIGGER"
    )
    return RejectedL3Candidate(
        user_id=user_id,
        symbol=symbol,
        direction=direction,
        timeframe=timeframe,
        candle_open_timestamp=candle_open_for_timeframe(observed_at, timeframe),
        observed_at=_utc(observed_at),
        profile_id=profile_id,
        profile_name=profile_name,
        profile_version=profile_version,
        profile_version_id=profile_version_id,
        decision_score=_as_float(decision.get("score")),
        buy_threshold=_as_float(buy_threshold),
        strong_buy_threshold=_as_float(strong_buy_threshold),
        decision=deepcopy(decision),
        rules_snapshot=deepcopy(rules_snapshot)
        if rules_snapshot is not None
        else None,
        market_structure_score=components.get("market_structure_score"),
        momentum_score=components.get("momentum_score"),
        liquidity_score=components.get("liquidity_score"),
        signal_score=components.get("signal_score"),
        watchlist_id=watchlist_id,
        watchlist_name=watchlist_name,
        watchlist_level=watchlist_level,
        source_watchlist_id=source_watchlist_id,
        rejection_stage=stage,
        rule_version=rule_version,
    )


def _groups(
    candidates: Iterable[RejectedL3Candidate],
) -> dict[tuple[str, str, str, str, datetime], list[RejectedL3Candidate]]:
    groups: dict[
        tuple[str, str, str, str, datetime], list[RejectedL3Candidate]
    ] = {}
    for candidate in candidates:
        key = (
            str(candidate.user_id),
            candidate.symbol,
            candidate.direction,
            candidate.timeframe,
            _utc(candidate.candle_open_timestamp),
        )
        groups.setdefault(key, []).append(candidate)
    return groups


def _profile_id(candidate: Optional[RejectedL3Candidate]) -> Optional[str]:
    if candidate is None or candidate.profile_id is None:
        return None
    return str(candidate.profile_id)


def _selection_metrics(candidate: RejectedL3Candidate) -> dict[str, float]:
    return {
        "normalized_score_margin": candidate.normalized_score_margin,
        "decision_score": candidate.decision_score,
        "market_structure_score": _as_float(candidate.market_structure_score),
        "momentum_score": _as_float(candidate.momentum_score),
        "liquidity_score": _as_float(candidate.liquidity_score),
        "signal_score": _as_float(candidate.signal_score),
    }


def _rejection_reasons(candidate: RejectedL3Candidate) -> Any:
    return deepcopy(candidate.decision.get("reasons") or [])


def _candidate_audit(candidate: RejectedL3Candidate, rank: int) -> dict[str, Any]:
    return {
        "rank": rank,
        "profile_id": _profile_id(candidate),
        "profile_name": candidate.profile_name,
        "profile_version": candidate.profile_version.isoformat()
        if isinstance(candidate.profile_version, datetime)
        else None,
        "profile_version_id": str(candidate.profile_version_id)
        if candidate.profile_version_id
        else None,
        "watchlist_id": candidate.watchlist_id,
        "watchlist_name": candidate.watchlist_name,
        "rejection_stage": candidate.rejection_stage,
        "rejection_reasons": _rejection_reasons(candidate),
        "selection_metrics": _selection_metrics(candidate),
    }


def _candidate_identity(candidate: RejectedL3Candidate) -> str:
    raw = "|".join(
        (
            candidate.event_id,
            _profile_id(candidate) or "",
            candidate.watchlist_id or "",
            candidate.symbol,
            candidate.direction,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _suppression_already_recorded(
    db,
    *,
    candidate: RejectedL3Candidate,
    reason_code: str,
) -> bool:
    result = await db.execute(
        text(
            """
            SELECT 1
              FROM decisions_log
             WHERE user_id = CAST(:user_id AS UUID)
               AND event_type = :event_type
               AND decision = 'SUPPRESSED'
               AND metrics->>'candidate_identity' = :candidate_identity
               AND metrics->>'reason_code' = :reason_code
             LIMIT 1
            """
        ),
        {
            "user_id": str(candidate.user_id),
            "event_type": SUPPRESSION_EVENT_TYPE,
            "candidate_identity": _candidate_identity(candidate),
            "reason_code": reason_code,
        },
    )
    return result.first() is not None


async def _record_suppressed(
    db,
    *,
    candidate: RejectedL3Candidate,
    reason_code: str,
    winner: Optional[RejectedL3Candidate],
    winner_trade_id: Any,
    candidate_rank: int,
    candidate_count: int,
    scan_run_id: str,
    rule_version: str,
) -> bool:
    if await _suppression_already_recorded(
        db, candidate=candidate, reason_code=reason_code
    ):
        return False
    metrics = {
        "consolidation_event_id": candidate.event_id,
        "candidate_identity": _candidate_identity(candidate),
        "scan_run_id": scan_run_id,
        "rule_version": rule_version,
        "reason_code": reason_code,
        "winner_profile_id": _profile_id(winner),
        "winner_profile_name": winner.profile_name if winner else None,
        "winner_trade_id": str(winner_trade_id) if winner_trade_id else None,
        "candidate_rank": candidate_rank,
        "candidate_count": candidate_count,
        "rejection_stage": candidate.rejection_stage,
        "rejection_reasons": _rejection_reasons(candidate),
        "selection_metrics": _selection_metrics(candidate),
    }
    db.add(
        DecisionLog(
            symbol=candidate.symbol,
            strategy="L3_REJECTED",
            timeframe=candidate.timeframe,
            score=candidate.decision_score,
            decision="SUPPRESSED",
            l1_pass=True,
            l2_pass=True,
            l3_pass=False,
            reasons={
                "consolidation": reason_code,
                "original": _rejection_reasons(candidate),
            },
            metrics=metrics,
            direction=candidate.direction,
            event_type=SUPPRESSION_EVENT_TYPE,
            user_id=candidate.user_id,
            profile_id=candidate.profile_id,
            profile_name=candidate.profile_name,
            profile_version=candidate.profile_version,
            created_at=datetime.now(timezone.utc),
        )
    )
    return True


def _lineage(candidate: RejectedL3Candidate) -> WatchlistLineageContext:
    return WatchlistLineageContext(
        watchlist_id=candidate.watchlist_id,
        watchlist_name=candidate.watchlist_name,
        watchlist_level=candidate.watchlist_level,
        source_watchlist_id=candidate.source_watchlist_id,
        profile_id=_profile_id(candidate),
        profile_name=candidate.profile_name,
        profile_version=candidate.profile_version,
        profile_version_id=str(candidate.profile_version_id)
        if candidate.profile_version_id
        else None,
        rules_snapshot=deepcopy(candidate.rules_snapshot)
        if candidate.rules_snapshot is not None
        else None,
        lineage_confidence="EXACT",
        lineage_source="l3_rejected_profile_consolidation",
        lineage_resolved_at=datetime.now(timezone.utc),
        ml_gate_enabled=False,
    )


async def _canonical_rate_limit(db, user_id: Any) -> int:
    row = await db.execute(
        select(ConfigProfile).where(
            ConfigProfile.user_id == user_id,
            ConfigProfile.config_type == "ml",
            ConfigProfile.is_active.is_(True),
        ).limit(1)
    )
    config_row = row.scalar_one_or_none()
    config = config_row.config_json if config_row else None
    if not isinstance(config, dict) or (
        "shadow_capture_l3_rejected_max_per_hour" not in config
    ):
        raise ValueError(
            "shadow_capture_l3_rejected_max_per_hour_missing"
        )
    value = int(config["shadow_capture_l3_rejected_max_per_hour"])
    if value < 0:
        raise ValueError("shadow_capture_l3_rejected_max_per_hour_invalid")
    return value


async def _canonical_created_last_hour(db, user_id: Any) -> int:
    result = await db.execute(
        text(
            """
            SELECT COUNT(*)
              FROM shadow_trades
             WHERE user_id = CAST(:user_id AS UUID)
               AND source = 'L3_REJECTED'
               AND l3_consolidation_enforced IS TRUE
               AND created_at > NOW() - INTERVAL '1 hour'
            """
        ),
        {"user_id": str(user_id)},
    )
    return int(result.scalar_one() or 0)


async def _record_all(
    db,
    *,
    ranked: list[RejectedL3Candidate],
    reason_code: str,
    winner: Optional[RejectedL3Candidate],
    winner_trade_id: Any,
    scan_run_id: str,
    rule_version: str,
) -> int:
    recorded = 0
    for rank, candidate in enumerate(ranked, start=1):
        recorded += int(
            await _record_suppressed(
                db,
                candidate=candidate,
                reason_code=reason_code,
                winner=winner,
                winner_trade_id=winner_trade_id,
                candidate_rank=rank,
                candidate_count=len(ranked),
                scan_run_id=scan_run_id,
                rule_version=rule_version,
            )
        )
    return recorded


async def consolidate_l3_rejected_candidates(
    candidates: Iterable[RejectedL3Candidate],
    *,
    scan_run_id: str,
    rule_version: Optional[str] = None,
) -> list[ConsolidationResult]:
    """Persist one rejected owner per event and audit every association."""
    candidate_rows = list(candidates)
    candidate_rule_versions = {
        candidate.rule_version for candidate in candidate_rows
    }
    if rule_version is not None:
        candidate_rule_versions.add(rule_version)
    if len(candidate_rule_versions) > 1:
        raise ValueError("mixed_l3_rejected_consolidation_rule_versions")
    rule_version = (
        next(iter(candidate_rule_versions))
        if candidate_rule_versions
        else RULE_VERSION_V1
    )
    if rule_version not in SUPPORTED_RULE_VERSIONS:
        raise ValueError(
            f"unsupported_l3_rejected_consolidation_rule_version:{rule_version}"
        )

    from ..database import CeleryAsyncSessionLocal
    from .shadow_trade_service import (
        SHADOW_SOURCE_L3_REJECTED,
        _SyntheticDecision,
        _create_from_decision,
        _load_strategy_lab_features_by_symbol,
        load_shadow_creation_config,
    )

    grouped = _groups(candidate_rows)
    if not grouped:
        return []

    features_by_symbol = await _load_strategy_lab_features_by_symbol(
        [candidate.symbol for values in grouped.values() for candidate in values]
    )
    config_by_user: dict[str, dict[str, Any]] = {}
    results: list[ConsolidationResult] = []

    for key in sorted(grouped, key=lambda item: tuple(map(str, item))):
        user_key, symbol, direction, timeframe, candle_open = key
        ranked = rank_candidates(grouped[key])
        winner = ranked[0]
        event_id = winner.event_id
        if user_key not in config_by_user:
            config_by_user[user_key] = await load_shadow_creation_config(
                winner.user_id
            )
        user_config = config_by_user[user_key]

        try:
            async with CeleryAsyncSessionLocal() as db:
                async with db.begin():
                    await acquire_consolidation_lock(
                        db,
                        user_id=winner.user_id,
                        symbol=symbol,
                        direction=direction,
                        lane=SOURCE,
                    )
                    active = await find_active_shadow_for_source(
                        db,
                        user_id=winner.user_id,
                        symbol=symbol,
                        direction=direction,
                        source=SOURCE,
                    )
                    if active is not None:
                        suppressed = await _record_all(
                            db,
                            ranked=ranked,
                            reason_code=REASON_ACTIVE_REJECTED,
                            winner=None,
                            winner_trade_id=active.id,
                            scan_run_id=scan_run_id,
                            rule_version=rule_version,
                        )
                        results.append(
                            ConsolidationResult(
                                event_id=event_id,
                                symbol=symbol,
                                direction=direction,
                                decision="SUPPRESSED",
                                reason_code=REASON_ACTIVE_REJECTED,
                                winner_profile_id=None,
                                trade_id=str(active.id),
                                candidate_count=len(ranked),
                                suppressed_count=suppressed,
                            )
                        )
                        continue

                    # Serialize the per-user rate budget after candidate
                    # consolidation. Only canonical winners are counted.
                    await db.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {
                            "lock_key": int.from_bytes(
                                hashlib.sha256(
                                    f"{winner.user_id}|{SOURCE}|RATE_LIMIT".encode(
                                        "utf-8"
                                    )
                                ).digest()[:8],
                                "big",
                                signed=True,
                            )
                        },
                    )
                    max_per_hour = await _canonical_rate_limit(db, winner.user_id)
                    created_last_hour = await _canonical_created_last_hour(
                        db, winner.user_id
                    )
                    if created_last_hour >= max_per_hour:
                        suppressed = await _record_all(
                            db,
                            ranked=ranked,
                            reason_code=REASON_RATE_LIMIT,
                            winner=None,
                            winner_trade_id=None,
                            scan_run_id=scan_run_id,
                            rule_version=rule_version,
                        )
                        results.append(
                            ConsolidationResult(
                                event_id=event_id,
                                symbol=symbol,
                                direction=direction,
                                decision="SUPPRESSED",
                                reason_code=REASON_RATE_LIMIT,
                                winner_profile_id=None,
                                trade_id=None,
                                candidate_count=len(ranked),
                                suppressed_count=suppressed,
                            )
                        )
                        continue

                    audits = [
                        _candidate_audit(candidate, rank)
                        for rank, candidate in enumerate(ranked, start=1)
                    ]
                    consolidation = {
                        "enabled": True,
                        "lane": SOURCE,
                        "rule_version": rule_version,
                        "event_id": event_id,
                        "scan_run_id": scan_run_id,
                        "timeframe": timeframe,
                        "candle_open": _utc(candle_open).isoformat(),
                        "candidate_count": len(ranked),
                        "associated_profile_count": max(len(ranked) - 1, 0),
                        "primary_profile_id": _profile_id(winner),
                        "primary_profile_name": winner.profile_name,
                        "primary_profile": audits[0],
                        "candidates": audits,
                        "associated_profiles": audits[1:],
                        "candidate_profile_ids": [
                            _profile_id(candidate) for candidate in ranked
                        ],
                        "candidate_profile_names": [
                            candidate.profile_name for candidate in ranked
                        ],
                        "suppressed_profile_ids": [
                            _profile_id(candidate) for candidate in ranked[1:]
                        ],
                        "suppressed_profile_names": [
                            candidate.profile_name for candidate in ranked[1:]
                        ],
                        "selection_rule": SELECTION_RULE,
                        "selection_metrics": _selection_metrics(winner),
                    }

                    metrics = dict(winner.decision.get("metrics") or {})
                    metrics.update(
                        {
                            "l3_decision": "BLOCK",
                            "l3_score": winner.decision_score,
                            "l3_reasons": _rejection_reasons(winner),
                            "source": "l3_rejected_consolidated",
                            "execution_id": scan_run_id,
                        }
                    )
                    canonical_features = features_by_symbol.get(symbol)
                    if canonical_features:
                        metrics["indicators_snapshot"] = dict(canonical_features)
                    asset = winner.decision.get("_asset") or {}
                    if isinstance(asset, dict):
                        price = asset.get("current_price") or asset.get("price")
                        if price is not None:
                            metrics.setdefault("current_price", price)

                    synthetic = _SyntheticDecision(
                        user_id=winner.user_id,
                        symbol=symbol,
                        direction=direction,
                        strategy=winner.decision.get("strategy") or "L3",
                        id=None,
                        created_at=winner.observed_at,
                        metrics=metrics,
                    )
                    trade_id = await _create_from_decision(
                        db,
                        synthetic,
                        "L3_REJECTED_PROFILE_CONSOLIDATION",
                        user_config,
                        source=SHADOW_SOURCE_L3_REJECTED,
                        extra_config={
                            "l3_decision": "BLOCK",
                            "l3_score": winner.decision_score,
                            "l3_reasons": _rejection_reasons(winner),
                            "consolidation": consolidation,
                        },
                        lineage=_lineage(winner),
                        consolidation_enforced=True,
                    )
                    if trade_id is None:
                        concurrent = await find_active_shadow_for_source(
                            db,
                            user_id=winner.user_id,
                            symbol=symbol,
                            direction=direction,
                            source=SOURCE,
                        )
                        if concurrent is None:
                            raise RuntimeError(
                                "rejected_consolidated_insert_returned_none_without_active"
                            )
                        suppressed = await _record_all(
                            db,
                            ranked=ranked,
                            reason_code=REASON_CONCURRENT_REJECTED,
                            winner=None,
                            winner_trade_id=concurrent.id,
                            scan_run_id=scan_run_id,
                            rule_version=rule_version,
                        )
                        results.append(
                            ConsolidationResult(
                                event_id=event_id,
                                symbol=symbol,
                                direction=direction,
                                decision="SUPPRESSED",
                                reason_code=REASON_CONCURRENT_REJECTED,
                                winner_profile_id=None,
                                trade_id=str(concurrent.id),
                                candidate_count=len(ranked),
                                suppressed_count=suppressed,
                            )
                        )
                        continue

                    suppressed = 0
                    for rank, candidate in enumerate(ranked[1:], start=2):
                        suppressed += int(
                            await _record_suppressed(
                                db,
                                candidate=candidate,
                                reason_code=REASON_LOWER_PRIORITY,
                                winner=winner,
                                winner_trade_id=trade_id,
                                candidate_rank=rank,
                                candidate_count=len(ranked),
                                scan_run_id=scan_run_id,
                                rule_version=rule_version,
                            )
                        )
                    logger.info(
                        "l3_rejected_profile_consolidation winner event_id=%s "
                        "symbol=%s direction=%s winner_profile=%s trade_id=%s "
                        "candidates=%d suppressed=%d rule_version=%s",
                        event_id,
                        symbol,
                        direction,
                        winner.profile_name,
                        trade_id,
                        len(ranked),
                        suppressed,
                        rule_version,
                    )
                    results.append(
                        ConsolidationResult(
                            event_id=event_id,
                            symbol=symbol,
                            direction=direction,
                            decision="CREATED",
                            reason_code=None,
                            winner_profile_id=_profile_id(winner),
                            trade_id=str(trade_id),
                            candidate_count=len(ranked),
                            suppressed_count=suppressed,
                        )
                    )
        except Exception:
            logger.exception(
                "l3_rejected_profile_consolidation failed event_id=%s "
                "symbol=%s direction=%s scan_run_id=%s",
                event_id,
                symbol,
                direction,
                scan_run_id,
            )
            results.append(
                ConsolidationResult(
                    event_id=event_id,
                    symbol=symbol,
                    direction=direction,
                    decision="ERROR",
                    reason_code="CONSOLIDATION_TRANSACTION_FAILED",
                    winner_profile_id=_profile_id(winner),
                    trade_id=None,
                    candidate_count=len(ranked),
                    suppressed_count=0,
                )
            )

    return results
