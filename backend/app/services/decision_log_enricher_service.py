"""Decision Log Enricher Service — Module 1.

Reads unprocessed ALLOW decisions from ``decisions_log``, derives a
simulated entry, and creates a corresponding ``trade_tracking`` record.

Invariants
----------
* Does NOT call the Gate.io API.
* Does NOT close trades or compute final P&L.
* Does NOT modify the original pipeline, indicators, or score engine.
* Each processed decision is marked ``processed = TRUE`` inside the same
  transaction as the ``trade_tracking`` insert — atomic and crash-safe.
* The ``ux_trade_tracking_decision`` unique index + ``ON CONFLICT DO NOTHING``
  guarantee true idempotency even if a crash occurs between the INSERT and the
  ``processed = TRUE`` update.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.backoffice import DecisionLog
from ..models.trade_tracking import TradeTracking

logger = logging.getLogger(__name__)

_BATCH_LIMIT = 100
# Fallback TP/SL percentages used when config_profiles has no enricher entry.
# Override via config_profiles with config_type='enricher_settings':
#   {"tp_pct": 0.01, "sl_pct": 0.01}
_DEFAULT_TP_PCT = 0.01
_DEFAULT_SL_PCT = 0.01


class DecisionLogEnricherService:
    """Enriches unprocessed ALLOW decisions by creating trade_tracking rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── public entry-point ────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Fetch unprocessed ALLOW decisions and create trade_tracking rows.

        Returns a summary dict with counts for logging / task result.
        """
        config = await self._load_config()
        decisions = await self._fetch_unprocessed_decisions()
        if not decisions:
            logger.debug("[Enricher] No unprocessed ALLOW decisions found.")
            return {"processed": 0, "skipped": 0, "errors": 0}

        processed = skipped = errors = 0
        for decision in decisions:
            # Task #237 — each decision runs inside its own SAVEPOINT so a
            # failure (e.g. ON CONFLICT planner mismatch on a partial index,
            # bad data, FK violation) rolls back ONLY this decision's work
            # without poisoning the outer ``run_db_task`` transaction. A
            # poisoned outer tx used to cascade ``PendingRollbackError``
            # into the next Celery task that picked up the same pooled
            # connection (decision_log_enricher → collect_5m → compute_5m
            # all share the ``microstructure`` queue in production).
            #
            # Golden rule: NEVER ``await session.rollback()`` inside
            # ``begin_nested()`` — the context manager already rolls back
            # the SAVEPOINT on exception; calling ``rollback()`` would
            # close the OUTER transaction opened by ``run_db_task``.
            try:
                async with self.session.begin_nested():
                    created = await self._process_decision(decision, config)
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
                # Mark as processed inside a FRESH savepoint so we never
                # write on top of an aborted transaction (which would
                # raise ``InFailedSQLTransactionError`` and re-poison the
                # outer tx — the original Task #237 cascade).
                try:
                    async with self.session.begin_nested():
                        await self._mark_processed(decision)
                except Exception as mark_exc:
                    logger.error(
                        "[Enricher] Failed to mark decision id=%s processed: %s",
                        decision.id,
                        mark_exc,
                        exc_info=True,
                    )

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
        from sqlalchemy import select
        result = await self.session.execute(
            select(DecisionLog)
            .where(DecisionLog.decision == "ALLOW", DecisionLog.processed.is_(False))
            .order_by(DecisionLog.created_at.asc())
            .limit(_BATCH_LIMIT)
        )
        return list(result.scalars().all())

    async def _process_decision(self, decision: DecisionLog, config: dict[str, Any]) -> bool:
        """Create a trade_tracking row for *decision* and mark it processed.

        Returns True when a row was inserted, False when skipped (invalid data).
        Raises ValueError for missing price so the caller's error counter is
        incremented and the decision is still marked processed.
        """
        # ── Fix 1: robust price extraction with two-level fallback ────────────
        # Try the top-level ``price`` attribute first (future-proof); then fall
        # back to metrics JSONB where pipeline_scan stores it today.
        entry_price = self._extract_price(decision)

        # ── Fix 3: consolidated upfront validation ────────────────────────────
        symbol: str = decision.symbol or ""
        entry_time: datetime | None = decision.created_at

        if not symbol:
            logger.warning(
                "[Enricher] Skipping decision id=%s — missing symbol",
                decision.id,
            )
            await self._mark_processed(decision)
            return False

        if entry_price is None:
            raise ValueError(
                f"Missing price in decision_log id={decision.id} symbol={symbol}"
            )

        if not entry_time:
            logger.warning(
                "[Enricher] Skipping decision id=%s symbol=%s — missing entry_time",
                decision.id,
                symbol,
            )
            await self._mark_processed(decision)
            return False

        market_type: str = self._extract_market_type(decision)
        position_side: str = self._extract_position_side(decision)

        tp_pct: float = float(config.get("tp_pct", _DEFAULT_TP_PCT))
        sl_pct: float = float(config.get("sl_pct", _DEFAULT_SL_PCT))
        target_price, stop_price = self._calc_target_stop(entry_price, position_side, tp_pct, sl_pct)

        # ── Fix 2: idempotent INSERT via ON CONFLICT DO NOTHING ───────────────
        # The unique index ux_trade_tracking_decision guarantees at-most-once
        # even if a crash occurred between a previous INSERT and its
        # processed=TRUE update.
        # Task #237 — ``ux_trade_tracking_decision`` is a PARTIAL unique
        # index (migration 038): ``ON trade_tracking (decision_id) WHERE
        # decision_id IS NOT NULL``. Postgres requires the same predicate
        # in ``ON CONFLICT`` so the planner can match the index, otherwise
        # it raises ``InvalidColumnReferenceError`` and aborts the tx.
        # Without this predicate the enricher poisoned its own
        # transaction every cycle and cascaded into every other task on
        # the ``microstructure`` Celery queue (collect_5m, compute_5m).
        stmt = (
            pg_insert(TradeTracking)
            .values(
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
            .on_conflict_do_nothing(
                index_elements=["decision_id"],
                index_where=TradeTracking.decision_id.is_not(None),
            )
        )
        await self.session.execute(stmt)

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

    async def _load_config(self) -> dict[str, Any]:
        """Load enricher config from config_profiles, falling back to defaults.

        Operators can tune TP/SL percentages by inserting a row with
        ``config_type = 'enricher_settings'`` in config_profiles:
        ``{"tp_pct": 0.015, "sl_pct": 0.01}``
        """
        try:
            result = await self.session.execute(
                text("""
                    SELECT config_json
                    FROM config_profiles
                    WHERE config_type = 'enricher_settings'
                    LIMIT 1
                """)
            )
            row = result.fetchone()
            if row and row.config_json:
                return row.config_json
        except Exception as exc:
            logger.warning("[Enricher] Could not load config_profiles: %s", exc)
        return {}

    @staticmethod
    def _extract_price(decision: DecisionLog) -> float | None:
        """Extract price with a two-level fallback.

        1. Direct ``price`` attribute on the ORM row (future-proof — if a
           top-level price column is ever added to decisions_log).
        2. ``metrics->>'price'`` JSONB key where pipeline_scan stores it today.
        """
        raw = getattr(decision, "price", None)
        if raw is None:
            metrics: dict = decision.metrics or {}
            raw = metrics.get("price")
        if raw is None:
            return None
        try:
            value = float(raw)
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
    def _calc_target_stop(
        entry_price: float,
        position_side: str,
        tp_pct: float = _DEFAULT_TP_PCT,
        sl_pct: float = _DEFAULT_SL_PCT,
    ) -> tuple[float, float]:
        """Return (target_price, stop_price) based on entry, side, and config pcts."""
        if position_side == "short":
            target = entry_price * (1 - tp_pct)
            stop = entry_price * (1 + sl_pct)
        else:
            target = entry_price * (1 + tp_pct)
            stop = entry_price * (1 - sl_pct)
        return target, stop
