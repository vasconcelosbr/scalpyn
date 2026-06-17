"""profile_metrics and rule_contribution — performance cache tables

profile_metrics: daily aggregated performance per profile (win_rate, pnl, TTT, MAE/MFE).
rule_contribution: per-rule win/loss attribution — which rules correlate with profits.

Both tables are write-only caches refreshed by the daily intelligence job.
They are never the source of truth; shadow_trades is the source of truth.

Revision ID: 083_profile_metrics_tables
Revises: 082_decision_profile_link
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "083_profile_metrics_tables"
down_revision = "082_decision_profile_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_metrics (
            id                          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                     UUID        NOT NULL,
            profile_id                  UUID        NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            profile_name                VARCHAR(255) NULL,
            source                      VARCHAR(30) NULL,
            period_start                TIMESTAMPTZ NULL,
            period_end                  TIMESTAMPTZ NULL,
            total_trades                INTEGER     NOT NULL DEFAULT 0,
            closed_trades               INTEGER     NOT NULL DEFAULT 0,
            open_trades                 INTEGER     NOT NULL DEFAULT 0,
            wins                        INTEGER     NOT NULL DEFAULT 0,
            losses                      INTEGER     NOT NULL DEFAULT 0,
            timeouts                    INTEGER     NOT NULL DEFAULT 0,
            win_rate                    NUMERIC(8,4) NULL,
            pnl_total_pct               NUMERIC(12,4) NULL,
            avg_pnl_pct                 NUMERIC(8,4) NULL,
            avg_holding_seconds         NUMERIC(12,2) NULL,
            avg_winner_holding_seconds  NUMERIC(12,2) NULL,
            avg_mae_pct                 NUMERIC(8,4) NULL,
            avg_mfe_pct                 NUMERIC(8,4) NULL,
            tp_15m_rate                 NUMERIC(8,4) NULL,
            tp_30m_rate                 NUMERIC(8,4) NULL,
            tp_60m_rate                 NUMERIC(8,4) NULL,
            confidence_level            VARCHAR(20) NULL,
            extra_json                  JSONB       NULL,
            calculated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_profile_metrics_profile_period
        ON profile_metrics (user_id, profile_id, period_end DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_profile_metrics_calculated
        ON profile_metrics (user_id, calculated_at DESC)
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS rule_contribution (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID        NOT NULL,
            profile_id          UUID        NULL REFERENCES profiles(id) ON DELETE SET NULL,
            rule_hash           VARCHAR(64) NOT NULL,
            rule_type           VARCHAR(30) NULL,
            indicator           VARCHAR(60) NULL,
            operator            VARCHAR(10) NULL,
            value_text          VARCHAR(60) NULL,
            bucket_label        VARCHAR(60) NULL,
            total_cases         INTEGER     NOT NULL DEFAULT 0,
            wins                INTEGER     NOT NULL DEFAULT 0,
            losses              INTEGER     NOT NULL DEFAULT 0,
            win_rate            NUMERIC(8,4) NULL,
            avg_pnl_pct         NUMERIC(8,4) NULL,
            avg_mae_pct         NUMERIC(8,4) NULL,
            avg_mfe_pct         NUMERIC(8,4) NULL,
            lift_vs_base        NUMERIC(8,4) NULL,
            confidence_score    NUMERIC(8,4) NULL,
            extra_json          JSONB       NULL,
            calculated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_rule_contribution_profile
        ON rule_contribution (user_id, profile_id, calculated_at DESC)
        WHERE profile_id IS NOT NULL
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_rule_contribution_hash
        ON rule_contribution (rule_hash)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS rule_contribution"))
    op.execute(sa.text("DROP TABLE IF EXISTS profile_metrics"))
