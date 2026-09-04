"""R1 cutover: promote the validated 1m/5m/30m closed-candle capture to
the canonical ``ohlcv`` table (CHECK is_closed + provenance columns) and
register the CANONICAL contract row.

The historical-row provenance backfill (marking pre-cutover 1m/5m rows as
``legacy_collect_market_data_untrusted``) is intentionally NOT done in this
migration -- it is an unbounded UPDATE across ~2.6M rows and would hold the
migration transaction open far longer than a deploy should block on, risking
lock contention with the live collectors. It runs as a separate, batched,
idempotent follow-up (see R1 cutover report) after this migration is deployed
and healthy.

Revision ID: 214_ohlcv_canonical_cutover
Revises: 213_settlement_latency_anchor
"""

from alembic import op
import sqlalchemy as sa


revision = "214_ohlcv_canonical_cutover"
down_revision = "213_settlement_latency_anchor"
branch_labels = None
depends_on = None


CAPTURE_CONTRACT_VERSION = "gate_ohlcv_canonical_v1"


def upgrade() -> None:
    # Historical `ohlcv` rows (established since before this contract system
    # existed) were only ever written from Gate's is_closed=true candles --
    # DEFAULT TRUE is a factual backfill, not an assumption, and matches the
    # invariant this CHECK now enforces going forward.
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        ADD COLUMN IF NOT EXISTS is_closed BOOLEAN NOT NULL DEFAULT TRUE
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        ADD COLUMN IF NOT EXISTS ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        ADD COLUMN IF NOT EXISTS capture_contract_version VARCHAR(80) NULL
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        DROP CONSTRAINT IF EXISTS ck_ohlcv_closed
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        ADD CONSTRAINT ck_ohlcv_closed CHECK (is_closed IS TRUE)
    """))

    # Same future-valid_from buffer pattern as 208/209 -- gives the API
    # deploy time to roll out before any closed candle is admitted under
    # this contract.
    op.execute(sa.text("""
        INSERT INTO ohlcv_capture_contracts
            (capture_contract_version, valid_from, mode, source, timeframes,
             closed_table, live_table, canonical_read_enabled,
             finalization_delay_seconds)
        VALUES
            (:version, clock_timestamp() + INTERVAL '5 minutes',
             'CANONICAL', 'gate.io', '["1m", "5m", "30m"]'::jsonb,
             'ohlcv', 'ohlcv_live', TRUE, 60)
        ON CONFLICT (capture_contract_version) DO NOTHING
    """).bindparams(version=CAPTURE_CONTRACT_VERSION))


def downgrade() -> None:
    op.execute(
        sa.text("""
            DELETE FROM ohlcv_capture_contracts
             WHERE capture_contract_version = :version
        """).bindparams(version=CAPTURE_CONTRACT_VERSION)
    )
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        DROP CONSTRAINT IF EXISTS ck_ohlcv_closed
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        DROP COLUMN IF EXISTS capture_contract_version
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        DROP COLUMN IF EXISTS ingested_at
    """))
    op.execute(sa.text("""
        ALTER TABLE ohlcv
        DROP COLUMN IF EXISTS is_closed
    """))
