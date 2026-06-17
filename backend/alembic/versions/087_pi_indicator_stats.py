"""profile_indicator_stats — per-indicator bucketed statistics from a PI run.

Revision ID: 087_pi_indicator_stats
Revises: 086_pi_runs
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "087_pi_indicator_stats"
down_revision = "086_pi_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_indicator_stats (
            id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                     UUID        NOT NULL,
            run_id                      UUID        NOT NULL
                REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE,
            indicator                   VARCHAR(60) NOT NULL,
            operator                    VARCHAR(10),
            range_min                   NUMERIC,
            range_max                   NUMERIC,
            value_text                  VARCHAR(60),
            bucket_label                VARCHAR(100) NOT NULL,
            total_cases                 INTEGER     DEFAULT 0,
            wins                        INTEGER     DEFAULT 0,
            losses                      INTEGER     DEFAULT 0,
            timeouts                    INTEGER     DEFAULT 0,
            win_rate                    NUMERIC,
            loss_rate                   NUMERIC,
            avg_pnl_pct                 NUMERIC,
            avg_holding_seconds         NUMERIC,
            avg_winner_holding_seconds  NUMERIC,
            avg_mae_pct                 NUMERIC,
            avg_mfe_pct                 NUMERIC,
            tp_15m_rate                 NUMERIC,
            tp_30m_rate                 NUMERIC,
            tp_60m_rate                 NUMERIC,
            lift_vs_base                NUMERIC,
            pnl_lift_vs_base            NUMERIC,
            winner_presence_pct         NUMERIC,
            loser_presence_pct          NUMERIC,
            confidence_score            NUMERIC,
            confidence_level            VARCHAR(20),
            role_detected               VARCHAR(30),
            source_profiles             JSONB,
            evidence_json               JSONB,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_run
        ON profile_indicator_stats (user_id, run_id)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_role
        ON profile_indicator_stats (user_id, role_detected, confidence_score DESC)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_ind_stats_bucket
        ON profile_indicator_stats (indicator, bucket_label)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS profile_indicator_stats"))
