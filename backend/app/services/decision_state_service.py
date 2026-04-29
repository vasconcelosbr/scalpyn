"""Decision State Service - Business logic for state-based decision logging.

High-level service that coordinates:
- State engine (business rules)
- State repository (database persistence)
- Decision logging (when appropriate)

Main entry point for pipeline integration.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .decision_state_engine import (
    DecisionStateEngine,
    OpportunityState,
    STATE_ACTIVE,
    STATE_IDLE,
    STATE_CLOSED,
)
from .decision_state_repository import StateRepository

logger = logging.getLogger(__name__)


class DecisionStateService:
    """Service for managing decision state lifecycle and preventing duplicates."""

    def __init__(
        self,
        db: AsyncSession,
        cooldown_minutes: int = 30,
        stale_minutes: int = 60,
    ):
        self.db = db
        self.engine = DecisionStateEngine(cooldown_minutes=cooldown_minutes)
        self.repository = StateRepository(db)
        self.stale_minutes = stale_minutes

    async def process_decisions(
        self,
        decisions: List[dict],
        user_id: UUID,
    ) -> Tuple[List[dict], dict]:
        """Process a batch of decisions and determine which should be logged.

        Returns:
            (decisions_to_log, stats)

        Where decisions_to_log contains only decisions that should create new log entries,
        with added fields:
        - decision_group_id: UUID linking related decisions
        - state_hash: Hash identifying the opportunity state
        """
        if not decisions:
            return [], {"total": 0, "logged": 0, "held": 0, "closed": 0}

        stats = {
            "total": len(decisions),
            "logged": 0,
            "held": 0,
            "closed": 0,
            "transitions": {
                "created": 0,
                "held": 0,
                "closed": 0,
                "reopened": 0,
            },
        }

        # Extract symbols and strategy for bulk lookup
        symbols = [d.get("symbol") for d in decisions]
        strategy = decisions[0].get("strategy", "SPOT")  # Assume uniform strategy per batch

        # Bulk load current states
        current_states = await self.repository.get_states_bulk(user_id, symbols, strategy)

        decisions_to_log = []
        states_to_upsert = []

        for decision in decisions:
            symbol = decision.get("symbol")
            current_state = current_states.get(symbol)

            # Evaluate state transition
            should_log, group_id, state_hash = self.engine.should_log_decision(
                decision,
                current_state,
                user_id,
            )

            new_state, _, _ = self.engine.evaluate_state_transition(
                current_state,
                decision,
                user_id,
            )

            # Track transition types for monitoring
            if should_log:
                if current_state is None or current_state.is_idle():
                    stats["transitions"]["created"] += 1
                elif current_state.is_closed():
                    stats["transitions"]["reopened"] += 1
                stats["logged"] += 1
            elif new_state == STATE_ACTIVE:
                stats["transitions"]["held"] += 1
                stats["held"] += 1
            elif new_state == STATE_CLOSED:
                stats["transitions"]["closed"] += 1
                stats["closed"] += 1

            # Add state tracking fields to decision if logging
            if should_log:
                decision["decision_group_id"] = group_id
                decision["state_hash"] = state_hash
                decisions_to_log.append(decision)

            # Create/update opportunity state
            opportunity = self.engine.create_opportunity_state(
                decision,
                user_id,
                new_state,
                decision_group_id=group_id if should_log else (current_state.metadata.get("decision_group_id") if current_state else None),
                decision_id=None,  # Will be set after decision is persisted
            )
            states_to_upsert.append(opportunity)

        # Bulk persist all state updates
        if states_to_upsert:
            await self.repository.bulk_upsert_states(states_to_upsert)

        logger.info(
            "[StateService] Processed %d decisions: %d to log, %d held, %d closed | "
            "Transitions: %d created, %d held, %d closed, %d reopened",
            stats["total"],
            stats["logged"],
            stats["held"],
            stats["closed"],
            stats["transitions"]["created"],
            stats["transitions"]["held"],
            stats["transitions"]["closed"],
            stats["transitions"]["reopened"],
        )

        return decisions_to_log, stats

    async def update_decision_ids(
        self,
        decisions_with_ids: List[Tuple[str, int]],  # (symbol, decision_id)
        user_id: UUID,
        strategy: str,
    ) -> None:
        """Update decision IDs in opportunity states after decisions are persisted."""
        for symbol, decision_id in decisions_with_ids:
            await self.repository.transition_state(
                user_id=user_id,
                symbol=symbol,
                strategy=strategy,
                new_state=STATE_ACTIVE,  # Keep as active
                decision_id=decision_id,
            )

    async def cleanup_stale_opportunities(self) -> dict:
        """Mark stale opportunities as CLOSED and delete old CLOSED records.

        Should be called periodically (e.g., every scan cycle or hourly).
        """
        stale_count = await self.repository.cleanup_stale_states(self.stale_minutes)
        deleted_count = await self.repository.delete_old_closed_states(days_old=7)

        return {
            "stale_marked_closed": stale_count,
            "old_deleted": deleted_count,
        }

    async def get_active_opportunities(
        self,
        user_id: UUID,
        strategy: Optional[str] = None,
    ) -> List[dict]:
        """Get all currently active opportunities for monitoring."""
        opportunities = await self.repository.get_active_opportunities(user_id, strategy)

        return [
            self.engine.format_state_summary(opp)
            for opp in opportunities
        ]

    async def get_state_for_symbol(
        self,
        user_id: UUID,
        symbol: str,
        strategy: str,
    ) -> Optional[dict]:
        """Get current state for a specific symbol."""
        state = await self.repository.get_state(user_id, symbol, strategy)
        if state:
            return self.engine.format_state_summary(state)
        return None

    async def force_close_opportunity(
        self,
        user_id: UUID,
        symbol: str,
        strategy: str,
    ) -> bool:
        """Manually close an opportunity (admin/debugging tool)."""
        return await self.repository.transition_state(
            user_id=user_id,
            symbol=symbol,
            strategy=strategy,
            new_state=STATE_CLOSED,
        )

    async def reset_opportunity_state(
        self,
        user_id: UUID,
        symbol: str,
        strategy: str,
    ) -> bool:
        """Reset an opportunity to IDLE state (admin/debugging tool)."""
        return await self.repository.transition_state(
            user_id=user_id,
            symbol=symbol,
            strategy=strategy,
            new_state=STATE_IDLE,
            state_hash=None,
            decision_id=None,
        )

    async def get_statistics(self, user_id: UUID) -> dict:
        """Get state statistics for monitoring dashboard."""
        return await self.repository.get_state_statistics(user_id)

    async def analyze_duplicate_risk(
        self,
        user_id: UUID,
        strategy: str,
        lookback_hours: int = 1,
    ) -> dict:
        """Analyze potential duplicate decisions in recent history.

        Useful for validating the state engine is working correctly.
        """
        from sqlalchemy import select, func, and_
        from ..models.backoffice import DecisionLog

        cutoff = datetime.now(timezone.utc).replace(hour=datetime.now(timezone.utc).hour - lookback_hours)

        # Find symbols with multiple ALLOW decisions in the lookback period
        stmt = (
            select(
                DecisionLog.symbol,
                func.count().label("decision_count"),
                func.count(func.distinct(DecisionLog.state_hash)).label("unique_hashes"),
            )
            .where(
                and_(
                    DecisionLog.user_id == user_id,
                    DecisionLog.strategy == strategy,
                    DecisionLog.decision == "ALLOW",
                    DecisionLog.created_at >= cutoff,
                )
            )
            .group_by(DecisionLog.symbol)
            .having(func.count() > 1)
        )

        result = await self.db.execute(stmt)
        rows = result.all()

        duplicates = [
            {
                "symbol": row.symbol,
                "decision_count": row.decision_count,
                "unique_hashes": row.unique_hashes,
                "likely_duplicate": row.unique_hashes == 1,  # Same hash = duplicate
            }
            for row in rows
        ]

        return {
            "lookback_hours": lookback_hours,
            "symbols_with_multiple_decisions": len(duplicates),
            "likely_duplicates": sum(1 for d in duplicates if d["likely_duplicate"]),
            "details": duplicates,
        }
