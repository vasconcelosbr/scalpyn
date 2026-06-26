"""profile_intelligence_runs — tracks each Profile Intelligence Engine execution.

Revision ID: 086_pi_runs
Revises: 085_profile_audit_log
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "086_pi_runs"
down_revision = "085_profile_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_runs (
            id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                     UUID        NOT NULL,
            run_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            lookback_days               INTEGER     NOT NULL,
            min_closed_trades           INTEGER     NOT NULL DEFAULT 30,
            discovery_start_at          TIMESTAMPTZ,
            discovery_end_at            TIMESTAMPTZ,
            validation_start_at         TIMESTAMPTZ,
            validation_end_at           TIMESTAMPTZ,
            profiles_analyzed           JSONB,
            total_profiles              INTEGER     DEFAULT 0,
            total_shadow_trades         INTEGER     DEFAULT 0,
            total_closed_trades         INTEGER     DEFAULT 0,
            total_opportunity_snapshots INTEGER     DEFAULT 0,
            base_win_rate               NUMERIC,
            base_avg_pnl_pct            NUMERIC,
            base_tp_30m_rate            NUMERIC,
            status                      VARCHAR(30) DEFAULT 'running',
            engine_version              VARCHAR(30),
            settings_json               JSONB,
            notes                       TEXT,
            error_message               TEXT,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_runs_user_run_at
        ON profile_intelligence_runs (user_id, run_at DESC)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_runs_user_status
        ON profile_intelligence_runs (user_id, status, run_at DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS profile_intelligence_runs"))
