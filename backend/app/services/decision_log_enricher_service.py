"""Decision Log Enricher Service — Module 1.

Reads unprocessed ALLOW decisions from ``decisions_log``, derives a
simulated entry, and creates a corresponding ``trade_tracking`` record.

Invariants
----------
* Does NOT call the Gate.io API.
* Does NOT close trades or compute final P&L.
* Does NOT modify the original pipeline, indicators, or score engine.
* Each processed decision is marked ``processed = TRUE`` exactly once
  inside the same transaction as the ``trade_tracking`` insert, so the
  operation is atomic and idempotent against crash-restart.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.backoffice import DecisionLog
from ..models.trade_tracking import TradeTracking

logger = logging.getLogger(__name__)

_BATCH_LIMIT = 100


class DecisionLogEnricherService:
    """Enriches unprocessed ALLOW decisions by creating trade_tracking rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── public entry-point ────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Fetch unprocessed ALLOW decisions and create trade_tracking rows.

        Returns a summary dict with counts for logging / task result.
        """
        decisions = await self._fetch_unprocessed_decisions()
        if not decisions:
            logger.debug("[Enricher] No unprocessed ALLOW decisions found.")
            return {"processed": 0, "skipped": 0, "errors": 0}

        processed = skipped = errors = 0
        for decision in decisions:
            try:
                created = await self._process_decision(decision)
                if created:
                    processed += 1
                else:
                    skipped += 1
            except Exception as exc:
                errors += 1
                logger.error(
                    "[Enricher] Failed to process decision id=%s symbol=%s: %s",
                    decision.id,
                    decision.symbol,
                    exc,
                    exc_info=True,
                )
                # Mark as processed anyway to avoid infinite retry on bad rows.
                await self._mark_processed(decision)

        logger.info(
            "[Enricher] Complete: processed=%d skipped=%d errors=%d",
            processed,
            skipped,
            errors,
        )
        return {"processed": processed, "skipped": skipped, "errors": errors}

    # ── internals ─────────────────────────────────────────────────────────────

    async def _fetch_unprocessed_decisions(self) -> list[DecisionLog]:
        """Return up to _BATCH_LIMIT unprocessed ALLOW decisions, oldest first."""
        result = await self.session.execute(
            select(DecisionLog)
            .where(DecisionLog.decision == "ALLOW", DecisionLog.processed == False)  # noqa: E712
            .order_by(DecisionLog.created_at.asc())
            .limit(_BATCH_LIMIT)
        )
        return list(result.scalars().all())

    async def _process_decision(self, decision: DecisionLog) -> bool:
        """Create a trade_tracking row for *decision* and mark it processed.

        Returns True when a row was inserted, False when skipped (e.g. no
        price available in metrics).
        """
        entry_price = self._extract_price(decision)
        if entry_price is None:
            logger.warning(
                "[Enricher] Skipping decision id=%s symbol=%s — no price in metrics",
                decision.id,
                decision.symbol,
            )
            await self._mark_processed(decision)
            return False

        entry_time: datetime = decision.created_at or datetime.now(timezone.utc)
        symbol: str = decision.symbol
        market_type: str = self._extract_market_type(decision)
        position_side: str = self._extract_position_side(decision)

        target_price, stop_price = self._calc_target_stop(entry_price, position_side)

        tracking = TradeTracking(
            decision_id=decision.id,
            symbol=symbol,
            market_type=market_type,
            position_side=position_side,
            is_simulated=True,
            entry_price=entry_price,
            entry_time=entry_time,
            target_price=target_price,
            stop_price=stop_price,
            status="open",
        )
        self.session.add(tracking)

        await self._mark_processed(decision)

        logger.info(
            "[Enricher] Created trade_tracking | symbol=%s market=%s side=%s "
            "entry=%.8g target=%.8g stop=%.8g decision_id=%s",
            symbol,
            market_type,
            position_side,
            float(entry_price),
            float(target_price),
            float(stop_price),
            decision.id,
        )
        return True

    async def _mark_processed(self, decision: DecisionLog) -> None:
        """Set decisions_log.processed = TRUE for *decision*."""
        await self.session.execute(
            update(DecisionLog)
            .where(DecisionLog.id == decision.id)
            .values(processed=True)
        )

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_price(decision: DecisionLog) -> float | None:
        """Read price from decisions_log.metrics JSONB."""
        metrics: dict = decision.metrics or {}
        price = metrics.get("price")
        if price is None:
            return None
        try:
            value = float(price)
            return value if value > 0 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_market_type(decision: DecisionLog) -> str:
        metrics: dict = decision.metrics or {}
        return str(metrics.get("market_type", "spot"))

    @staticmethod
    def _extract_position_side(decision: DecisionLog) -> str:
        direction = (decision.direction or "").lower()
        if direction == "short":
            return "short"
        return "long"

    @staticmethod
    def _calc_target_stop(entry_price: float, position_side: str) -> tuple[float, float]:
        """Return (target_price, stop_price) based on entry and side."""
        if position_side == "short":
            target = entry_price * 0.99
            stop = entry_price * 1.01
        else:
            target = entry_price * 1.01
            stop = entry_price * 0.99
        return target, stop
