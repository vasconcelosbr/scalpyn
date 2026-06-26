"""profile_rule_combinations — discovered rule combos ranked by champion score.

Revision ID: 088_pi_rule_combinations
Revises: 087_pi_indicator_stats
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "088_pi_rule_combinations"
down_revision = "087_pi_indicator_stats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_rule_combinations (
            id                                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                             UUID        NOT NULL,
            run_id                              UUID        NOT NULL
                REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE,
            combination_hash                    VARCHAR(64) NOT NULL,
            combination_type                    VARCHAR(30) NOT NULL,
            setup_family                        VARCHAR(30),
            suggested_name                      VARCHAR(120),
            rules_json                          JSONB       NOT NULL DEFAULT '[]'::jsonb,
            signals_json                        JSONB,
            scoring_rules_json                  JSONB,
            block_rules_json                    JSONB,
            required_master_scoring_rules_json  JSONB,
            source_profiles                     JSONB,
            total_cases                         INTEGER     DEFAULT 0,
            wins                                INTEGER     DEFAULT 0,
            losses                              INTEGER     DEFAULT 0,
            timeouts                            INTEGER     DEFAULT 0,
            win_rate                            NUMERIC,
            loss_rate                           NUMERIC,
            avg_pnl_pct                         NUMERIC,
            avg_holding_seconds                 NUMERIC,
            avg_winner_holding_seconds          NUMERIC,
            avg_mae_pct                         NUMERIC,
            avg_mfe_pct                         NUMERIC,
            tp_15m_rate                         NUMERIC,
            tp_30m_rate                         NUMERIC,
            tp_60m_rate                         NUMERIC,
            lift_vs_base                        NUMERIC,
            support                             NUMERIC,
            confidence                          NUMERIC,
            rule_lift                           NUMERIC,
            leverage                            NUMERIC,
            conviction                          NUMERIC,
            champion_score                      NUMERIC,
            confidence_level                    VARCHAR(20),
            discovery_metrics_json              JSONB,
            validation_metrics_json             JSONB,
            degradation_pct                     NUMERIC,
            overfit_risk                        BOOLEAN     DEFAULT FALSE,
            is_tested_live_shadow               BOOLEAN     DEFAULT FALSE,
            status                              VARCHAR(30) DEFAULT 'discovered',
            created_at                          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_comb_run
        ON profile_rule_combinations (user_id, run_id)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_comb_score
        ON profile_rule_combinations (user_id, champion_score DESC)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_comb_conf_score
        ON profile_rule_combinations (user_id, confidence_level, champion_score DESC)
    """))

    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_pi_comb_hash
        ON profile_rule_combinations (user_id, run_id, combination_hash)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS profile_rule_combinations"))
