"""Persist robust-pipeline outputs to the ``indicator_snapshots`` table.

The table is created lazily by :func:`ensure_snapshot_table` so the shadow
runner stays non-fatal even on a database where Alembic has not yet caught
up. ``persist_snapshot`` opens its own short transaction (via the supplied
session factory) so it never participates in the legacy pipeline's session
state.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .envelope import IndicatorEnvelope
from .score import ScoreResult
from .validation import ValidationResult

logger = logging.getLogger(__name__)


_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS indicator_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    symbol VARCHAR(40) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    indicators_json JSONB NOT NULL,
    global_confidence NUMERIC(6,4),
    valid_indicators INTEGER,
    total_indicators INTEGER,
    validation_passed BOOLEAN,
    validation_errors JSONB,
    score NUMERIC(7,4),
    score_confidence NUMERIC(6,4),
    can_trade BOOLEAN,
    legacy_score NUMERIC(7,4),
    divergence_bucket VARCHAR(16),
    rejection_reason VARCHAR(255),
    user_id UUID,
    watchlist_id UUID
);
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_symbol_time
    ON indicator_snapshots (symbol, timestamp DESC);
"""


_table_ready_lock = False  # in-process flag; per-worker is fine.


async def ensure_snapshot_table(db: AsyncSession) -> None:
    """Best-effort creation of the snapshot table + index.

    Production environments will get the table from Alembic migration 027 —
    this function exists so dev / fresh DBs (where ``alembic upgrade head``
    has not yet run for the new revision) still receive snapshots.
    """
    global _table_ready_lock
    if _table_ready_lock:
        return
    try:
        async with db.begin_nested():
            await db.execute(text(_TABLE_DDL))
            await db.execute(text(_INDEX_DDL))
        _table_ready_lock = True
    except Exception as exc:
        logger.debug("[robust_indicators] snapshot table create failed: %s", exc)


async def persist_snapshot(
    db: AsyncSession,
    *,
    symbol: str,
    envelopes: Mapping[str, IndicatorEnvelope],
    validation: ValidationResult,
    score: ScoreResult,
    legacy_score: Optional[float] = None,
    divergence_bucket: Optional[str] = None,
    user_id: Optional[uuid.UUID] = None,
    watchlist_id: Optional[uuid.UUID] = None,
    timestamp: Optional[datetime] = None,
) -> Optional[uuid.UUID]:
    """Insert a single snapshot row. Returns the new row id (or None on error)."""
    await ensure_snapshot_table(db)

    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    payload = {name: env.to_dict() for name, env in envelopes.items()}
    new_id = uuid.uuid4()

    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    """
                    INSERT INTO indicator_snapshots (
                        id, symbol, timestamp,
                        indicators_json, global_confidence,
                        valid_indicators, total_indicators,
                        validation_passed, validation_errors,
                        score, score_confidence, can_trade,
                        legacy_score, divergence_bucket, rejection_reason,
                        user_id, watchlist_id
                    ) VALUES (
                        :id, :symbol, :timestamp,
                        CAST(:indicators_json AS jsonb), :global_confidence,
                        :valid_indicators, :total_indicators,
                        :validation_passed, CAST(:validation_errors AS jsonb),
                        :score, :score_confidence, :can_trade,
                        :legacy_score, :divergence_bucket, :rejection_reason,
                        :user_id, :watchlist_id
                    )
                    """
                ),
                {
                    "id": new_id,
                    "symbol": symbol,
                    "timestamp": timestamp,
                    "indicators_json": json.dumps(payload, default=str),
                    "global_confidence": float(score.global_confidence),
                    "valid_indicators": int(score.valid_indicators),
                    "total_indicators": int(score.total_indicators),
                    "validation_passed": bool(validation.passed),
                    "validation_errors": json.dumps(
                        {
                            "errors": validation.errors,
                            "warnings": validation.warnings,
                        }
                    ),
                    "score": float(score.score),
                    "score_confidence": float(score.score_confidence),
                    "can_trade": bool(score.can_trade),
                    "legacy_score": float(legacy_score) if legacy_score is not None else None,
                    "divergence_bucket": divergence_bucket,
                    "rejection_reason": score.rejection_reason,
                    "user_id": user_id,
                    "watchlist_id": watchlist_id,
                },
            )
        return new_id
    except Exception as exc:
        logger.debug(
            "[robust_indicators] snapshot insert failed for %s: %s",
            symbol, exc,
        )
        return None


__all__ = ["ensure_snapshot_table", "persist_snapshot"]
