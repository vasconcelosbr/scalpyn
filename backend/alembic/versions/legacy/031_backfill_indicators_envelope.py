"""Backfill flat-format indicators rows to IndicatorEnvelope format.

Revision ID: 031
Revises: 030
Create Date: 2026-05-02

Context
-------
Prior to Task #160, all three write paths (compute_indicators.py,
structural_scheduler_service.py, microstructure_scheduler_service.py)
persisted flat scalar values in indicators.indicators_json:

    {"taker_ratio": 0.61, "rsi": 52.3, ...}

Task #160 updated every write path to use the IndicatorEnvelope format:

    {"taker_ratio": {"value": 0.61, "source": "gate_trades",
                     "confidence": 1.0, "status": "VALID"}, ...}

This migration backfills the existing flat rows so that audit queries
such as ``indicators_json->'taker_ratio'->>'source'`` work on historical
data.  It uses conservative fallback metadata (source="unknown",
confidence=0.5, status="UNKNOWN") since the original provenance is lost.

Idempotency
-----------
The UPDATE is gated on ``EXISTS (SELECT 1 FROM jsonb_each(indicators_json)
WHERE jsonb_typeof(value) != 'object')`` so rows already in envelope
format are untouched.  Running this migration twice is safe.

Performance note
----------------
The ``indicators`` table is a TimescaleDB hypertable partitioned by time.
If the table is very large (> 10 M rows), set ``statement_timeout=300s``
before running this migration in production to avoid lock contention on
older chunks.  The migration is non-destructive — existing data is
rewritten in-place per row.
"""

from alembic import op
import sqlalchemy as sa

revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        UPDATE indicators
        SET indicators_json = (
            SELECT jsonb_object_agg(
                kv.key,
                CASE
                    WHEN jsonb_typeof(kv.value) = 'object' THEN kv.value
                    ELSE jsonb_build_object(
                        'value',      kv.value,
                        'source',     'unknown',
                        'confidence', 0.5,
                        'status',     'UNKNOWN'
                    )
                END
            )
            FROM jsonb_each(indicators_json) AS kv
        )
        WHERE indicators_json IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM jsonb_each(indicators_json) AS kv2
              WHERE jsonb_typeof(kv2.value) != 'object'
          )
    """))


def downgrade() -> None:
    # Unwrap: extract only the 'value' field from each envelope object.
    # This loses source / confidence / status metadata irreversibly.
    op.execute(sa.text("""
        UPDATE indicators
        SET indicators_json = (
            SELECT jsonb_object_agg(
                kv.key,
                CASE
                    WHEN jsonb_typeof(kv.value) = 'object' AND kv.value ? 'value'
                        THEN kv.value -> 'value'
                    ELSE kv.value
                END
            )
            FROM jsonb_each(indicators_json) AS kv
        )
        WHERE indicators_json IS NOT NULL
    """))
