"""Persist robust-pipeline outputs to the ``indicator_snapshots`` table.

The table is created lazily by :func:`ensure_snapshot_table` so the
runner stays non-fatal even on a database where Alembic has not yet caught
up. ``persist_snapshot`` opens its own short transaction (via the supplied
session) so it never participates in the legacy pipeline's session state.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Mapping, Optional

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
_snapshot_columns: Optional[set[str]] = None


async def ensure_snapshot_table(db: AsyncSession) -> None:
    """Best-effort creation of the snapshot table + index.

    Production environments will get the table from Alembic — this function
    exists so dev / fresh DBs (where ``alembic upgrade head`` has not yet
    run for the new revision) still receive snapshots.
    """
    global _table_ready_lock, _snapshot_columns
    if _table_ready_lock and _snapshot_columns is not None:
        return
    try:
        async with db.begin_nested():
            await db.execute(text(_TABLE_DDL))
            await db.execute(text(_INDEX_DDL))
        async with db.begin_nested():
            rows = (await db.execute(
                text(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public'
                      AND table_name = 'indicator_snapshots'
                    """
                )
            )).fetchall()
        _snapshot_columns = {str(row[0]) for row in rows}
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
    user_id: Optional[uuid.UUID] = None,
    watchlist_id: Optional[uuid.UUID] = None,
    timestamp: Optional[datetime] = None,
) -> Optional[object]:
    """Insert a single snapshot row. Returns the new row id (or None on error)."""
    await ensure_snapshot_table(db)

    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    payload = {name: env.to_dict() for name, env in envelopes.items()}
    base_columns = [
        "symbol", "timestamp", "indicators_json", "global_confidence",
        "valid_indicators", "total_indicators", "validation_passed",
        "validation_errors", "score", "score_confidence", "can_trade",
    ]
    value_sql = [
        ":symbol", ":timestamp", "CAST(:indicators_json AS jsonb)",
        ":global_confidence", ":valid_indicators", ":total_indicators",
        ":validation_passed", "CAST(:validation_errors AS jsonb)",
        ":score", ":score_confidence", ":can_trade",
    ]
    params = {
        "symbol": symbol,
        "timestamp": timestamp,
        "indicators_json": json.dumps(payload, default=str),
        "global_confidence": float(score.global_confidence),
        "valid_indicators": int(score.valid_indicators),
        "total_indicators": int(score.total_indicators),
        "validation_passed": bool(validation.passed),
        "validation_errors": json.dumps(
            {"errors": validation.errors, "warnings": validation.warnings}
        ),
        "score": float(score.score),
        "score_confidence": float(score.score_confidence),
        "can_trade": bool(score.can_trade),
    }

    available = _snapshot_columns or set()
    optional_values = {
        "rejection_reason": score.rejection_reason,
        "user_id": user_id,
        "watchlist_id": watchlist_id,
    }
    for column, value in optional_values.items():
        if column in available:
            base_columns.append(column)
            value_sql.append(f":{column}")
            params[column] = value

    insert_sql = text(
        f"""
        INSERT INTO indicator_snapshots ({', '.join(base_columns)})
        VALUES ({', '.join(value_sql)})
        RETURNING id
        """
    )

    try:
        async with db.begin_nested():
            result = await db.execute(insert_sql, params)
            new_id = result.scalar_one()
        return new_id
    except Exception as exc:
        logger.debug(
            "[robust_indicators] snapshot insert failed for %s: %s",
            symbol, exc,
        )
        return None


__all__ = ["ensure_snapshot_table", "persist_snapshot"]
