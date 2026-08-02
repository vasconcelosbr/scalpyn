"""Deterministic, transactional consolidation of canonical L3 profile trades.

All profiles are evaluated upstream.  This service receives only the approved
candidates from one scanner run, ranks candidates belonging to the same market
event, and creates at most one canonical ``source='L3'`` shadow per
``(user, symbol, direction)`` while the feature flag is enabled.

Suppressed candidates are audit rows in ``decisions_log``; they never become
shadow trades and therefore never receive barriers, PnL, or training labels.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import select, text

from ..models.backoffice import DecisionLog
from ..models.shadow_trade import ShadowTrade
from ..schemas.watchlist_lineage_context import WatchlistLineageContext
from . import l3_consolidation_metrics

logger = logging.getLogger(__name__)

RULE_VERSION_V1 = "single_profile_per_symbol_v1"
SUPPORTED_RULE_VERSIONS = frozenset({RULE_VERSION_V1})
ACTIVE_SHADOW_STATUSES = ("PENDING", "RUNNING")

REASON_ACTIVE_TRADE = "ACTIVE_TRADE_ALREADY_EXISTS"
REASON_LOWER_PRIORITY = "SAME_SYMBOL_LOWER_PRIORITY"
REASON_CONCURRENT_TRADE = "CONCURRENT_ACTIVE_TRADE_CREATED"
SUPPRESSION_EVENT_TYPE = "PROFILE_CONSOLIDATION"


def _as_float(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def candle_open_for_timeframe(at: datetime, timeframe: str) -> datetime:
    """Floor a decision timestamp to the opening instant of its candle."""
    normalized = (timeframe or "5m").strip().lower()
    multiplier = 1
    unit = normalized[-1:] or "m"
    try:
        multiplier = max(int(normalized[:-1]), 1)
    except (TypeError, ValueError):
        multiplier = 5
        unit = "m"
    seconds = multiplier * (3600 if unit == "h" else 60)
    timestamp = int(_utc(at).timestamp())
    return datetime.fromtimestamp(timestamp - (timestamp % seconds), tz=timezone.utc)


def build_consolidation_event_id(
    *,
    symbol: str,
    direction: str,
    timeframe: str,
    candle_open_timestamp: datetime,
) -> str:
    canonical = "|".join(
        (
            "L3",
            symbol.upper(),
            direction.upper(),
            timeframe.lower(),
            _utc(candle_open_timestamp).isoformat().replace("+00:00", "Z"),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_advisory_lock_key(user_id: Any, symbol: str, direction: str) -> int:
    """Return a stable signed bigint accepted by ``pg_advisory_xact_lock``."""
    raw = f"{user_id}|L3|{symbol.upper()}|{direction.upper()}".encode("utf-8")
    unsigned = int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")
    return unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned


@dataclass(frozen=True)
class EligibleL3Candidate:
    user_id: Any
    decision_id: int
    symbol: str
    direction: str
    timeframe: str
    candle_open_timestamp: datetime
    profile_id: Optional[Any]
    profile_name: Optional[str]
    profile_version: Optional[datetime]
    decision_score: float
    buy_threshold: float
    strong_buy_threshold: float
    market_structure_score: Optional[float] = None
    momentum_score: Optional[float] = None
    liquidity_score: Optional[float] = None
    signal_score: Optional[float] = None
    watchlist_id: Optional[str] = None
    watchlist_name: Optional[str] = None
    watchlist_level: Optional[str] = None
    source_watchlist_id: Optional[str] = None
    ml_score: Optional[dict[str, Any]] = None

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
        )


@dataclass(frozen=True)
class ConsolidationResult:
    event_id: str
    symbol: str
    direction: str
    decision: str
    reason_code: Optional[str]
    winner_profile_id: Optional[str]
    trade_id: Optional[str]
    candidate_count: int
    suppressed_count: int


def rank_candidates(
    candidates: Iterable[EligibleL3Candidate],
) -> list[EligibleL3Candidate]:
    """Apply the v1 ordering contract with deterministic final tie-breakers."""
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.normalized_score_margin,
            -_as_float(candidate.decision_score),
            -_as_float(candidate.market_structure_score),
            -_as_float(candidate.momentum_score),
            -_as_float(candidate.liquidity_score),
            -_as_float(candidate.signal_score),
            (candidate.profile_name or "").casefold(),
            str(candidate.profile_id or ""),
        ),
    )


def selection_thresholds(
    *,
    profile_config: Optional[dict],
    score_config: Optional[dict],
    spot_buy_threshold: float,
    spot_strong_buy_threshold: float,
) -> tuple[float, float]:
    """Resolve profile-specific bands before falling back to Spot DB config."""
    profile_thresholds = ((profile_config or {}).get("scoring") or {}).get(
        "thresholds"
    ) or {}
    score_thresholds = (score_config or {}).get("thresholds") or {}
    thresholds = profile_thresholds or score_thresholds
    buy = thresholds.get("buy", thresholds.get("buy_threshold"))
    strong = thresholds.get(
        "strong_buy", thresholds.get("strong_buy_threshold")
    )
    return (
        _as_float(buy if buy is not None else spot_buy_threshold),
        _as_float(
            strong if strong is not None else spot_strong_buy_threshold
        ),
    )


def candidate_from_decision(
    *,
    user_id: Any,
    decision_id: int,
    decision: dict[str, Any],
    buy_threshold: float,
    strong_buy_threshold: float,
    profile_id: Optional[Any],
    profile_name: Optional[str],
    profile_version: Optional[datetime],
    watchlist_id: Optional[str],
    watchlist_name: Optional[str],
    watchlist_level: Optional[str],
    source_watchlist_id: Optional[str],
    ml_score: Optional[dict[str, Any]] = None,
) -> EligibleL3Candidate:
    metrics = decision.get("metrics") or {}
    components = metrics.get("score_components") or {}
    created_at = decision.get("created_at") or datetime.now(timezone.utc)
    timeframe = str(decision.get("timeframe") or "5m")
    return EligibleL3Candidate(
        user_id=user_id,
        decision_id=int(decision_id),
        symbol=str(decision.get("symbol") or "").upper(),
        direction=str(decision.get("direction") or "SPOT").upper(),
        timeframe=timeframe,
        candle_open_timestamp=candle_open_for_timeframe(created_at, timeframe),
        profile_id=profile_id,
        profile_name=profile_name,
        profile_version=profile_version,
        decision_score=_as_float(decision.get("score")),
        buy_threshold=_as_float(buy_threshold),
        strong_buy_threshold=_as_float(strong_buy_threshold),
        market_structure_score=components.get("market_structure_score"),
        momentum_score=components.get("momentum_score"),
        liquidity_score=components.get("liquidity_score"),
        signal_score=components.get("signal_score"),
        watchlist_id=watchlist_id,
        watchlist_name=watchlist_name,
        watchlist_level=watchlist_level,
        source_watchlist_id=source_watchlist_id,
        ml_score=ml_score,
    )


async def acquire_consolidation_lock(
    db,
    *,
    user_id: Any,
    symbol: str,
    direction: str,
) -> None:
    await db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": build_advisory_lock_key(user_id, symbol, direction)},
    )


async def find_active_l3_shadow(
    db,
    *,
    user_id: Any,
    symbol: str,
    direction: str,
) -> Optional[ShadowTrade]:
    result = await db.execute(
        select(ShadowTrade)
        .where(
            ShadowTrade.user_id == user_id,
            ShadowTrade.symbol == symbol,
            ShadowTrade.direction == direction,
            ShadowTrade.source == "L3",
            ShadowTrade.status.in_(ACTIVE_SHADOW_STATUSES),
        )
        .order_by(ShadowTrade.created_at.asc(), ShadowTrade.id.asc())
        .limit(1)
        .with_for_update()
    )
    return result.scalar_one_or_none()


def _candidate_profile_id(candidate: EligibleL3Candidate) -> Optional[str]:
    return str(candidate.profile_id) if candidate.profile_id else None


def _selection_metrics(candidate: EligibleL3Candidate) -> dict[str, float]:
    return {
        "normalized_score_margin": candidate.normalized_score_margin,
        "decision_score": candidate.decision_score,
        "market_structure_score": _as_float(candidate.market_structure_score),
        "momentum_score": _as_float(candidate.momentum_score),
        "liquidity_score": _as_float(candidate.liquidity_score),
        "signal_score": _as_float(candidate.signal_score),
    }


async def _suppression_already_recorded(
    db,
    *,
    event_id: str,
    candidate: EligibleL3Candidate,
    reason_code: str,
) -> bool:
    row = await db.execute(
        text(
            """
            SELECT 1
              FROM decisions_log
             WHERE user_id = CAST(:user_id AS UUID)
               AND event_type = :event_type
               AND decision = 'SUPPRESSED'
               AND metrics->>'consolidation_event_id' = :event_id
               AND metrics->>'source_decision_id' = :source_decision_id
               AND metrics->>'reason_code' = :reason_code
             LIMIT 1
            """
        ),
        {
            "user_id": str(candidate.user_id),
            "event_type": SUPPRESSION_EVENT_TYPE,
            "event_id": event_id,
            "source_decision_id": str(candidate.decision_id),
            "reason_code": reason_code,
        },
    )
    return row.first() is not None


async def _record_suppressed(
    db,
    *,
    candidate: EligibleL3Candidate,
    event_id: str,
    reason_code: str,
    winner: Optional[EligibleL3Candidate],
    winner_trade_id: Any,
    candidate_rank: int,
    candidate_count: int,
    scan_run_id: str,
    rule_version: str,
) -> bool:
    if await _suppression_already_recorded(
        db,
        event_id=event_id,
        candidate=candidate,
        reason_code=reason_code,
    ):
        return False
    metrics = {
        "consolidation_event_id": event_id,
        "scan_run_id": scan_run_id,
        "rule_version": rule_version,
        "reason_code": reason_code,
        "source_decision_id": str(candidate.decision_id),
        "winner_profile_id": _candidate_profile_id(winner) if winner else None,
        "winner_profile_name": winner.profile_name if winner else None,
        "winner_trade_id": str(winner_trade_id) if winner_trade_id else None,
        "normalized_score_margin": candidate.normalized_score_margin,
        "decision_score": candidate.decision_score,
        "candidate_rank": candidate_rank,
        "candidate_count": candidate_count,
    }
    db.add(
        DecisionLog(
            symbol=candidate.symbol,
            strategy="L3",
            timeframe=candidate.timeframe,
            score=candidate.decision_score,
            decision="SUPPRESSED",
            l1_pass=True,
            l2_pass=True,
            # The profile itself passed L3; only trade creation was suppressed.
            l3_pass=True,
            reasons={"consolidation": reason_code},
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


def _lineage(candidate: EligibleL3Candidate) -> WatchlistLineageContext:
    ml_score = candidate.ml_score or {}
    return WatchlistLineageContext(
        watchlist_id=candidate.watchlist_id,
        watchlist_name=candidate.watchlist_name,
        watchlist_level=candidate.watchlist_level,
        source_watchlist_id=candidate.source_watchlist_id,
        profile_id=_candidate_profile_id(candidate),
        profile_name=candidate.profile_name,
        profile_version=candidate.profile_version,
        lineage_confidence="EXACT",
        lineage_source="l3_profile_consolidation",
        lineage_resolved_at=datetime.now(timezone.utc),
        ml_model_id=ml_score.get("model_id"),
        ml_probability=ml_score.get("probability"),
        model_lane=ml_score.get("model_lane"),
        ranking_id=ml_score.get("ranking_id"),
        model_version=ml_score.get("model_version"),
        threshold_used=ml_score.get("threshold"),
        score_status=ml_score.get("score_status"),
        gate_action=ml_score.get("gate_action"),
        reason_codes=ml_score.get("reason_codes"),
        orchestrator_payload=ml_score.get("orchestrator_payload"),
        ml_gate_enabled=bool(ml_score),
    )


def _event_groups(
    candidates: Iterable[EligibleL3Candidate],
) -> dict[tuple[str, str, str, str, datetime], list[EligibleL3Candidate]]:
    groups: dict[
        tuple[str, str, str, str, datetime], list[EligibleL3Candidate]
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


async def consolidate_l3_candidates(
    candidates: Iterable[EligibleL3Candidate],
    *,
    scan_run_id: str,
    rule_version: str = RULE_VERSION_V1,
) -> list[ConsolidationResult]:
    """Create winners and suppression audits, one transaction per event group."""
    if rule_version not in SUPPORTED_RULE_VERSIONS:
        raise ValueError(f"unsupported_l3_consolidation_rule_version:{rule_version}")

    from ..database import CeleryAsyncSessionLocal
    from .shadow_trade_service import (
        SHADOW_SOURCE_L3,
        _create_from_decision,
        load_shadow_creation_config,
    )

    groups = _event_groups(candidates)
    if not groups:
        return []

    config_by_user: dict[str, dict[str, Any]] = {}
    results: list[ConsolidationResult] = []
    for key in sorted(groups, key=lambda item: tuple(map(str, item))):
        user_key, symbol, direction, _timeframe, _candle = key
        ranked = rank_candidates(groups[key])
        winner = ranked[0]
        event_id = winner.event_id
        candidate_count = len(ranked)
        l3_consolidation_metrics.record_candidates(
            symbol, candidate_count, rule_version
        )
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
                    )
                    active = await find_active_l3_shadow(
                        db,
                        user_id=winner.user_id,
                        symbol=symbol,
                        direction=direction,
                    )
                    if active is not None:
                        recorded = 0
                        for rank, candidate in enumerate(ranked, start=1):
                            recorded += int(
                                await _record_suppressed(
                                    db,
                                    candidate=candidate,
                                    event_id=event_id,
                                    reason_code=REASON_ACTIVE_TRADE,
                                    winner=None,
                                    winner_trade_id=active.id,
                                    candidate_rank=rank,
                                    candidate_count=candidate_count,
                                    scan_run_id=scan_run_id,
                                    rule_version=rule_version,
                                )
                            )
                        l3_consolidation_metrics.record_active_block(
                            symbol, rule_version
                        )
                        l3_consolidation_metrics.record_suppressed(
                            symbol, recorded, REASON_ACTIVE_TRADE, rule_version
                        )
                        logger.info(
                            "l3_profile_consolidation active_trade_block "
                            "event_id=%s symbol=%s direction=%s candidates=%d "
                            "existing_trade_id=%s rule_version=%s",
                            event_id,
                            symbol,
                            direction,
                            candidate_count,
                            active.id,
                            rule_version,
                        )
                        results.append(
                            ConsolidationResult(
                                event_id=event_id,
                                symbol=symbol,
                                direction=direction,
                                decision="SUPPRESSED",
                                reason_code=REASON_ACTIVE_TRADE,
                                winner_profile_id=None,
                                trade_id=str(active.id),
                                candidate_count=candidate_count,
                                suppressed_count=candidate_count,
                            )
                        )
                        continue

                    decision_row = (
                        await db.execute(
                            select(DecisionLog).where(
                                DecisionLog.id == winner.decision_id,
                                DecisionLog.user_id == winner.user_id,
                            )
                        )
                    ).scalar_one_or_none()
                    if decision_row is None:
                        raise RuntimeError(
                            f"winner_decision_not_found:{winner.decision_id}"
                        )

                    suppressed = ranked[1:]
                    consolidation_metadata = {
                        "enabled": True,
                        "rule_version": rule_version,
                        "event_id": event_id,
                        "scan_run_id": scan_run_id,
                        "candidate_count": candidate_count,
                        "primary_profile_id": _candidate_profile_id(winner),
                        "primary_profile_name": winner.profile_name,
                        "candidate_profile_ids": [
                            _candidate_profile_id(candidate) for candidate in ranked
                        ],
                        "candidate_profile_names": [
                            candidate.profile_name for candidate in ranked
                        ],
                        "suppressed_profile_ids": [
                            _candidate_profile_id(candidate)
                            for candidate in suppressed
                        ],
                        "suppressed_profile_names": [
                            candidate.profile_name for candidate in suppressed
                        ],
                        "selection_rule": [
                            "normalized_score_margin_desc",
                            "decision_score_desc",
                            "market_structure_score_desc",
                            "momentum_score_desc",
                            "liquidity_score_desc",
                            "signal_score_desc",
                            "profile_name_asc",
                            "profile_id_asc",
                        ],
                        "selection_metrics": _selection_metrics(winner),
                    }
                    trade_id = await _create_from_decision(
                        db,
                        decision_row,
                        "NOT_TRADABLE",
                        user_config,
                        source=SHADOW_SOURCE_L3,
                        extra_config={"consolidation": consolidation_metadata},
                        lineage=_lineage(winner),
                        consolidation_enforced=True,
                    )
                    if trade_id is None:
                        concurrent = await find_active_l3_shadow(
                            db,
                            user_id=winner.user_id,
                            symbol=symbol,
                            direction=direction,
                        )
                        if concurrent is None:
                            raise RuntimeError(
                                "consolidated_shadow_insert_returned_none_without_active_trade"
                            )
                        recorded = 0
                        for rank, candidate in enumerate(ranked, start=1):
                            recorded += int(
                                await _record_suppressed(
                                    db,
                                    candidate=candidate,
                                    event_id=event_id,
                                    reason_code=REASON_CONCURRENT_TRADE,
                                    winner=None,
                                    winner_trade_id=concurrent.id,
                                    candidate_rank=rank,
                                    candidate_count=candidate_count,
                                    scan_run_id=scan_run_id,
                                    rule_version=rule_version,
                                )
                            )
                        l3_consolidation_metrics.record_concurrency_conflict(
                            symbol, rule_version
                        )
                        l3_consolidation_metrics.record_suppressed(
                            symbol,
                            recorded,
                            REASON_CONCURRENT_TRADE,
                            rule_version,
                        )
                        results.append(
                            ConsolidationResult(
                                event_id=event_id,
                                symbol=symbol,
                                direction=direction,
                                decision="SUPPRESSED",
                                reason_code=REASON_CONCURRENT_TRADE,
                                winner_profile_id=None,
                                trade_id=str(concurrent.id),
                                candidate_count=candidate_count,
                                suppressed_count=candidate_count,
                            )
                        )
                        continue

                    recorded = 0
                    for rank, candidate in enumerate(suppressed, start=2):
                        recorded += int(
                            await _record_suppressed(
                                db,
                                candidate=candidate,
                                event_id=event_id,
                                reason_code=REASON_LOWER_PRIORITY,
                                winner=winner,
                                winner_trade_id=trade_id,
                                candidate_rank=rank,
                                candidate_count=candidate_count,
                                scan_run_id=scan_run_id,
                                rule_version=rule_version,
                            )
                        )
                    l3_consolidation_metrics.record_winner(
                        symbol, winner.profile_name or "_unknown", rule_version
                    )
                    l3_consolidation_metrics.record_suppressed(
                        symbol, recorded, REASON_LOWER_PRIORITY, rule_version
                    )
                    logger.info(
                        "l3_profile_consolidation winner event_id=%s symbol=%s "
                        "direction=%s winner_profile=%s trade_id=%s candidates=%d "
                        "suppressed=%d rule_version=%s",
                        event_id,
                        symbol,
                        direction,
                        winner.profile_name,
                        trade_id,
                        candidate_count,
                        recorded,
                        rule_version,
                    )
                    results.append(
                        ConsolidationResult(
                            event_id=event_id,
                            symbol=symbol,
                            direction=direction,
                            decision="CREATED",
                            reason_code=None,
                            winner_profile_id=_candidate_profile_id(winner),
                            trade_id=str(trade_id),
                            candidate_count=candidate_count,
                            suppressed_count=len(suppressed),
                        )
                    )
        except Exception:
            # The transaction context rolls back both the winner and every
            # suppression row.  A caller can retry the same deterministic event.
            logger.exception(
                "l3_profile_consolidation failed event_id=%s symbol=%s "
                "direction=%s scan_run_id=%s",
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
                    winner_profile_id=_candidate_profile_id(winner),
                    trade_id=None,
                    candidate_count=candidate_count,
                    suppressed_count=0,
                )
            )
    return results
