"""Add indicator_snapshots table for robust-indicator shadow mode.

Revision ID: 027_indicator_snapshots
Revises: 026_decisions_log_direction_event_type
Create Date: 2026-05-01
"""

from alembic import op
import sqlalchemy as sa

revision = "027_indicator_snapshots"
down_revision = "026_decisions_log_direction_event_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    op.execute(sa.text(
        """
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
    ))
    op.execute(sa.text(
        """
        CREATE INDEX IF NOT EXISTS ix_indicator_snapshots_symbol_time
            ON indicator_snapshots (symbol, timestamp DESC);
        """
    ))
    # Best-effort TimescaleDB hypertable conversion. The legacy `indicators`
    # table is a hypertable on the same column, so where Timescale is
    # available we follow the same convention. Wrapped in a DO block so
    # missing extension is non-fatal.
    op.execute(sa.text(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
            ) THEN
                BEGIN
                    PERFORM create_hypertable(
                        'indicator_snapshots', 'timestamp',
                        if_not_exists => TRUE,
                        migrate_data => TRUE
                    );
                EXCEPTION WHEN OTHERS THEN
                    -- Hypertable conversion is best-effort; ignore failures.
                    NULL;
                END;
            END IF;
        END
        $$;
        """
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "DROP INDEX IF EXISTS ix_indicator_snapshots_symbol_time;"
    ))
    op.execute(sa.text(
        "DROP TABLE IF EXISTS indicator_snapshots;"
    ))
