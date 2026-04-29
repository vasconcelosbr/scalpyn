"""Decision State Engine - Prevents duplicate decision logging.

Transforms from event-based logging to state-based opportunity tracking.

State Model:
- IDLE: No opportunity detected
- ACTIVE: Opportunity detected and ongoing
- CLOSED: Opportunity ended

State Transitions:
- IDLE → ACTIVE: Asset reaches L3, passes all rules (CREATE new decision)
- ACTIVE → ACTIVE: Asset still meets conditions (HOLD - update last_seen_at, NO new decision)
- ACTIVE → IDLE: Asset no longer meets conditions (CLOSE opportunity)

Design Principles:
1. Each trading opportunity is recorded ONLY ONCE
2. State hash detects if scenario changed meaningfully
3. Cooldown period prevents re-triggering same state immediately
4. O(1) lookup per symbol via in-memory cache + DB persistence
5. Thread-safe for concurrent scans
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

# State constants
STATE_IDLE = "IDLE"
STATE_ACTIVE = "ACTIVE"
STATE_CLOSED = "CLOSED"

# Default configuration values (can be overridden per-profile)
DEFAULT_COOLDOWN_MINUTES = 30
DEFAULT_STATE_HASH_THRESHOLD = 0.15  # 15% change in key features triggers new state


class OpportunityState:
    """Represents the current state of a trading opportunity for a symbol."""

    def __init__(
        self,
        symbol: str,
        strategy: str,
        user_id: UUID,
        state: str = STATE_IDLE,
        state_hash: Optional[str] = None,
        score: Optional[float] = None,
        started_at: Optional[datetime] = None,
        last_seen_at: Optional[datetime] = None,
        decision_id: Optional[int] = None,
        metadata: Optional[dict] = None,
    ):
        self.symbol = symbol
        self.strategy = strategy
        self.user_id = user_id
        self.state = state
        self.state_hash = state_hash
        self.score = score
        self.started_at = started_at
        self.last_seen_at = last_seen_at or datetime.now(timezone.utc)
        self.decision_id = decision_id
        self.metadata = metadata or {}

    def is_idle(self) -> bool:
        return self.state == STATE_IDLE

    def is_active(self) -> bool:
        return self.state == STATE_ACTIVE

    def is_closed(self) -> bool:
        return self.state == STATE_CLOSED

    def should_create_new_decision(
        self,
        new_hash: str,
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
    ) -> bool:
        """Determine if a new decision should be created.

        Returns True if:
        - State is IDLE, OR
        - State is ACTIVE but state_hash changed significantly, OR
        - State is CLOSED and cooldown period has passed
        """
        if self.is_idle():
            return True

        # If state hash changed, this is a new opportunity
        if self.state_hash and new_hash != self.state_hash:
            return True

        # If in cooldown after closing, check time elapsed
        if self.is_closed() and self.last_seen_at:
            elapsed = datetime.now(timezone.utc) - self.last_seen_at
            if elapsed > timedelta(minutes=cooldown_minutes):
                return True

        return False

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "user_id": str(self.user_id),
            "state": self.state,
            "state_hash": self.state_hash,
            "score": self.score,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "decision_id": self.decision_id,
            "metadata": self.metadata,
        }


class DecisionStateEngine:
    """Core engine for managing opportunity states and preventing duplicate decisions."""

    def __init__(
        self,
        cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
        hash_threshold: float = DEFAULT_STATE_HASH_THRESHOLD,
    ):
        self.cooldown_minutes = cooldown_minutes
        self.hash_threshold = hash_threshold
        logger.info(
            "[StateEngine] Initialized with cooldown=%d min, hash_threshold=%.2f",
            cooldown_minutes,
            hash_threshold,
        )

    def compute_state_hash(self, decision: dict) -> str:
        """Compute deterministic hash from key decision features.

        Includes:
        - Symbol
        - Score (rounded to 1 decimal to avoid micro-changes)
        - Key metrics that affect trading logic
        - Decision outcome
        """
        # Extract key features that define the opportunity
        features = {
            "symbol": decision.get("symbol"),
            "score": round(float(decision.get("score", 0)), 1),
            "decision": decision.get("decision"),
            "l3_pass": decision.get("l3_pass"),
        }

        # Include critical metrics from reasons
        reasons = decision.get("reasons") or {}
        critical_reasons = {
            k: v for k, v in reasons.items()
            if v == "OK" and k != "pipeline"  # Only include passing conditions
        }
        if critical_reasons:
            features["conditions"] = sorted(critical_reasons.keys())

        # Include key market metrics if available
        metrics = decision.get("metrics") or {}
        market_features = {}
        for key in ["price", "volume_24h", "market_cap", "rsi", "adx", "macd"]:
            if key in metrics and metrics[key] is not None:
                try:
                    # Round to reduce sensitivity to minor fluctuations
                    if key == "price":
                        market_features[key] = round(float(metrics[key]), 2)
                    elif key in ["rsi", "adx"]:
                        market_features[key] = round(float(metrics[key]), 0)
                    else:
                        market_features[key] = round(float(metrics[key]), -3)  # Round to thousands
                except (TypeError, ValueError):
                    pass

        if market_features:
            features["market"] = market_features

        # Generate hash
        canonical = json.dumps(features, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def evaluate_state_transition(
        self,
        current_state: Optional[OpportunityState],
        decision: dict,
        user_id: UUID,
    ) -> tuple[str, bool, Optional[str]]:
        """Evaluate what state transition should occur.

        Returns:
            (new_state, should_log_decision, decision_group_id)
        """
        symbol = decision.get("symbol")
        strategy = decision.get("strategy")
        new_hash = self.compute_state_hash(decision)
        now = datetime.now(timezone.utc)

        # Decision passed L3 conditions
        if decision.get("decision") == "ALLOW" and decision.get("l3_pass"):
            if current_state is None or current_state.is_idle():
                # CREATE: IDLE → ACTIVE
                logger.info(
                    "[StateEngine] %s: CREATE opportunity (IDLE → ACTIVE) | hash=%s",
                    symbol,
                    new_hash,
                )
                return STATE_ACTIVE, True, uuid4()

            elif current_state.is_active():
                # Check if state changed meaningfully
                if current_state.should_create_new_decision(new_hash, self.cooldown_minutes):
                    logger.info(
                        "[StateEngine] %s: State changed (hash: %s → %s) — creating NEW opportunity",
                        symbol,
                        current_state.state_hash,
                        new_hash,
                    )
                    return STATE_ACTIVE, True, uuid4()
                else:
                    # HOLD: ACTIVE → ACTIVE (same state)
                    logger.debug(
                        "[StateEngine] %s: HOLD opportunity (still ACTIVE) | hash=%s",
                        symbol,
                        new_hash,
                    )
                    return STATE_ACTIVE, False, None

            elif current_state.is_closed():
                # Check cooldown
                if current_state.should_create_new_decision(new_hash, self.cooldown_minutes):
                    logger.info(
                        "[StateEngine] %s: RE-ENTRY after cooldown (CLOSED → ACTIVE) | hash=%s",
                        symbol,
                        new_hash,
                    )
                    return STATE_ACTIVE, True, uuid4()
                else:
                    logger.debug(
                        "[StateEngine] %s: Still in cooldown period (%s remaining)",
                        symbol,
                        timedelta(minutes=self.cooldown_minutes) - (now - current_state.last_seen_at),
                    )
                    return STATE_CLOSED, False, None

        # Decision blocked or failed
        else:
            if current_state and current_state.is_active():
                # CLOSE: ACTIVE → CLOSED
                logger.info(
                    "[StateEngine] %s: CLOSE opportunity (ACTIVE → CLOSED) | reason=%s",
                    symbol,
                    decision.get("decision"),
                )
                return STATE_CLOSED, False, None
            else:
                # Remain IDLE or CLOSED
                return STATE_IDLE, False, None

        return STATE_IDLE, False, None

    def create_opportunity_state(
        self,
        decision: dict,
        user_id: UUID,
        state: str,
        decision_group_id: Optional[UUID] = None,
        decision_id: Optional[int] = None,
    ) -> OpportunityState:
        """Create a new OpportunityState from a decision."""
        now = datetime.now(timezone.utc)
        state_hash = self.compute_state_hash(decision)

        return OpportunityState(
            symbol=decision.get("symbol"),
            strategy=decision.get("strategy"),
            user_id=user_id,
            state=state,
            state_hash=state_hash,
            score=decision.get("score"),
            started_at=now if state == STATE_ACTIVE else None,
            last_seen_at=now,
            decision_id=decision_id,
            metadata={
                "decision_group_id": str(decision_group_id) if decision_group_id else None,
                "l1_pass": decision.get("l1_pass"),
                "l2_pass": decision.get("l2_pass"),
                "l3_pass": decision.get("l3_pass"),
            },
        )

    def should_log_decision(
        self,
        decision: dict,
        current_state: Optional[OpportunityState],
        user_id: UUID,
    ) -> tuple[bool, Optional[UUID], Optional[str]]:
        """Determine if a decision should be logged.

        Returns:
            (should_log, decision_group_id, state_hash)
        """
        new_state, should_log, group_id = self.evaluate_state_transition(
            current_state,
            decision,
            user_id,
        )

        if should_log:
            state_hash = self.compute_state_hash(decision)
            return True, group_id, state_hash

        return False, None, None

    def get_active_duration(self, opportunity: OpportunityState) -> Optional[timedelta]:
        """Calculate how long an opportunity has been active."""
        if opportunity.is_active() and opportunity.started_at:
            return datetime.now(timezone.utc) - opportunity.started_at
        return None

    def format_state_summary(self, opportunity: OpportunityState) -> dict:
        """Format opportunity state for monitoring/debugging."""
        duration = self.get_active_duration(opportunity)
        return {
            "symbol": opportunity.symbol,
            "strategy": opportunity.strategy,
            "state": opportunity.state,
            "score": opportunity.score,
            "state_hash": opportunity.state_hash,
            "started_at": opportunity.started_at.isoformat() if opportunity.started_at else None,
            "duration_minutes": int(duration.total_seconds() / 60) if duration else None,
            "last_seen": opportunity.last_seen_at.isoformat() if opportunity.last_seen_at else None,
        }
