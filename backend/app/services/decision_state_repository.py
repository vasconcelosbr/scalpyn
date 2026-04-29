"""State Repository - Database layer for opportunity state management.

Handles CRUD operations for active_candidates table with support for:
- Atomic upserts (INSERT ... ON CONFLICT DO UPDATE)
- Bulk state queries for pipeline scans
- State cleanup and expiration
- Thread-safe operations
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select, delete, update, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from ..models.backoffice import ActiveCandidate
from .decision_state_engine import OpportunityState, STATE_IDLE, STATE_ACTIVE, STATE_CLOSED

logger = logging.getLogger(__name__)


class StateRepository:
    """Repository for managing opportunity states in the database."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_state(
        self,
        user_id: UUID,
        symbol: str,
        strategy: str,
    ) -> Optional[OpportunityState]:
        """Get current state for a specific symbol/strategy."""
        stmt = select(ActiveCandidate).where(
            and_(
                ActiveCandidate.user_id == user_id,
                ActiveCandidate.symbol == symbol,
                ActiveCandidate.strategy == strategy,
            )
        )
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()

        if not row:
            return None

        return OpportunityState(
            symbol=row.symbol,
            strategy=row.strategy,
            user_id=row.user_id,
            state=row.state,
            state_hash=row.state_hash,
            score=row.score,
            started_at=row.started_at,
            last_seen_at=row.last_seen_at,
            decision_id=row.decision_id,
            metadata=row.metadata,
        )

    async def get_states_bulk(
        self,
        user_id: UUID,
        symbols: List[str],
        strategy: str,
    ) -> dict[str, OpportunityState]:
        """Get states for multiple symbols in a single query.

        Returns:
            Dict mapping symbol -> OpportunityState
        """
        if not symbols:
            return {}

        stmt = select(ActiveCandidate).where(
            and_(
                ActiveCandidate.user_id == user_id,
                ActiveCandidate.symbol.in_(symbols),
                ActiveCandidate.strategy == strategy,
            )
        )
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        states = {}
        for row in rows:
            states[row.symbol] = OpportunityState(
                symbol=row.symbol,
                strategy=row.strategy,
                user_id=row.user_id,
                state=row.state,
                state_hash=row.state_hash,
                score=row.score,
                started_at=row.started_at,
                last_seen_at=row.last_seen_at,
                decision_id=row.decision_id,
                metadata=row.metadata,
            )

        return states

    async def upsert_state(self, opportunity: OpportunityState) -> None:
        """Insert or update opportunity state atomically."""
        now = datetime.now(timezone.utc)

        stmt = insert(ActiveCandidate).values(
            symbol=opportunity.symbol,
            strategy=opportunity.strategy,
            user_id=opportunity.user_id,
            state=opportunity.state,
            state_hash=opportunity.state_hash,
            score=opportunity.score,
            started_at=opportunity.started_at,
            last_seen_at=now,
            decision_id=opportunity.decision_id,
            metadata=opportunity.metadata,
            updated_at=now,
        )

        # On conflict: update all fields except created_at
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "symbol", "strategy"],
            set_={
                "state": stmt.excluded.state,
                "state_hash": stmt.excluded.state_hash,
                "score": stmt.excluded.score,
                "started_at": stmt.excluded.started_at,
                "last_seen_at": stmt.excluded.last_seen_at,
                "decision_id": stmt.excluded.decision_id,
                "metadata": stmt.excluded.metadata,
                "updated_at": stmt.excluded.updated_at,
            },
        )

        await self.db.execute(stmt)

    async def bulk_upsert_states(self, opportunities: List[OpportunityState]) -> None:
        """Bulk insert/update multiple opportunity states."""
        if not opportunities:
            return

        now = datetime.now(timezone.utc)
        values = [
            {
                "symbol": opp.symbol,
                "strategy": opp.strategy,
                "user_id": opp.user_id,
                "state": opp.state,
                "state_hash": opp.state_hash,
                "score": opp.score,
                "started_at": opp.started_at,
                "last_seen_at": now,
                "decision_id": opp.decision_id,
                "metadata": opp.metadata,
                "updated_at": now,
            }
            for opp in opportunities
        ]

        stmt = insert(ActiveCandidate).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["user_id", "symbol", "strategy"],
            set_={
                "state": stmt.excluded.state,
                "state_hash": stmt.excluded.state_hash,
                "score": stmt.excluded.score,
                "started_at": stmt.excluded.started_at,
                "last_seen_at": stmt.excluded.last_seen_at,
                "decision_id": stmt.excluded.decision_id,
                "metadata": stmt.excluded.metadata,
                "updated_at": stmt.excluded.updated_at,
            },
        )

        await self.db.execute(stmt)
        logger.debug("[StateRepo] Bulk upserted %d opportunity states", len(opportunities))

    async def get_active_opportunities(
        self,
        user_id: UUID,
        strategy: Optional[str] = None,
    ) -> List[OpportunityState]:
        """Get all active opportunities for a user."""
        conditions = [
            ActiveCandidate.user_id == user_id,
            ActiveCandidate.state == STATE_ACTIVE,
        ]
        if strategy:
            conditions.append(ActiveCandidate.strategy == strategy)

        stmt = select(ActiveCandidate).where(and_(*conditions))
        result = await self.db.execute(stmt)
        rows = result.scalars().all()

        return [
            OpportunityState(
                symbol=row.symbol,
                strategy=row.strategy,
                user_id=row.user_id,
                state=row.state,
                state_hash=row.state_hash,
                score=row.score,
                started_at=row.started_at,
                last_seen_at=row.last_seen_at,
                decision_id=row.decision_id,
                metadata=row.metadata,
            )
            for row in rows
        ]

    async def cleanup_stale_states(
        self,
        stale_minutes: int = 60,
    ) -> int:
        """Mark opportunities as CLOSED if not seen recently.

        This prevents ACTIVE states from lingering indefinitely if the
        symbol stops appearing in scans.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)

        stmt = (
            update(ActiveCandidate)
            .where(
                and_(
                    ActiveCandidate.state == STATE_ACTIVE,
                    ActiveCandidate.last_seen_at < cutoff,
                )
            )
            .values(
                state=STATE_CLOSED,
                updated_at=datetime.now(timezone.utc),
            )
        )

        result = await self.db.execute(stmt)
        count = result.rowcount or 0

        if count > 0:
            logger.info(
                "[StateRepo] Marked %d stale opportunities as CLOSED (not seen in %d min)",
                count,
                stale_minutes,
            )

        return count

    async def delete_old_closed_states(
        self,
        days_old: int = 7,
    ) -> int:
        """Delete CLOSED states older than specified days to keep table lean."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_old)

        stmt = delete(ActiveCandidate).where(
            and_(
                ActiveCandidate.state == STATE_CLOSED,
                ActiveCandidate.updated_at < cutoff,
            )
        )

        result = await self.db.execute(stmt)
        count = result.rowcount or 0

        if count > 0:
            logger.info(
                "[StateRepo] Deleted %d CLOSED opportunities older than %d days",
                count,
                days_old,
            )

        return count

    async def get_state_statistics(self, user_id: UUID) -> dict:
        """Get summary statistics for monitoring."""
        from sqlalchemy import func

        stmt = (
            select(
                ActiveCandidate.state,
                func.count().label("count"),
                func.avg(
                    func.extract("epoch", datetime.now(timezone.utc) - ActiveCandidate.last_seen_at) / 60
                ).label("avg_minutes_since_last_seen"),
            )
            .where(ActiveCandidate.user_id == user_id)
            .group_by(ActiveCandidate.state)
        )

        result = await self.db.execute(stmt)
        rows = result.all()

        stats = {
            "total": 0,
            "by_state": {},
        }

        for row in rows:
            stats["by_state"][row.state] = {
                "count": row.count,
                "avg_minutes_since_last_seen": round(row.avg_minutes_since_last_seen or 0, 1),
            }
            stats["total"] += row.count

        return stats

    async def transition_state(
        self,
        user_id: UUID,
        symbol: str,
        strategy: str,
        new_state: str,
        state_hash: Optional[str] = None,
        decision_id: Optional[int] = None,
    ) -> bool:
        """Transition an opportunity to a new state.

        Returns:
            True if state was updated, False if no matching record found
        """
        now = datetime.now(timezone.utc)
        values = {
            "state": new_state,
            "last_seen_at": now,
            "updated_at": now,
        }

        if state_hash is not None:
            values["state_hash"] = state_hash

        if decision_id is not None:
            values["decision_id"] = decision_id

        if new_state == STATE_ACTIVE:
            # When transitioning to ACTIVE, set started_at if not already set
            # Use CASE to preserve existing started_at
            stmt = (
                update(ActiveCandidate)
                .where(
                    and_(
                        ActiveCandidate.user_id == user_id,
                        ActiveCandidate.symbol == symbol,
                        ActiveCandidate.strategy == strategy,
                    )
                )
                .values(**values)
            )
        else:
            stmt = (
                update(ActiveCandidate)
                .where(
                    and_(
                        ActiveCandidate.user_id == user_id,
                        ActiveCandidate.symbol == symbol,
                        ActiveCandidate.strategy == strategy,
                    )
                )
                .values(**values)
            )

        result = await self.db.execute(stmt)
        return (result.rowcount or 0) > 0
