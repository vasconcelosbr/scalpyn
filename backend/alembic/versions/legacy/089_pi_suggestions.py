"""profile_suggestions — AI-generated profile suggestions awaiting user approval.

Revision ID: 089_pi_suggestions
Revises: 088_pi_rule_combinations
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "089_pi_suggestions"
down_revision = "088_pi_rule_combinations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_suggestions (
            id                                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id                             UUID        NOT NULL,
            run_id                              UUID        NOT NULL
                REFERENCES profile_intelligence_runs(id) ON DELETE CASCADE,
            source_combination_id               UUID
                REFERENCES profile_rule_combinations(id) ON DELETE SET NULL,
            suggested_profile_name              VARCHAR(255) NOT NULL,
            suggested_profile_description       TEXT,
            suggested_profile_family            VARCHAR(30),
            source_profiles                     JSONB,
            suggested_config_json               JSONB       NOT NULL DEFAULT '{}'::jsonb,
            suggested_signals_json              JSONB,
            suggested_scoring_json              JSONB,
            suggested_block_rules_json          JSONB,
            required_master_scoring_rules_json  JSONB,
            evidence_summary_json               JSONB,
            quantitative_explanation            TEXT,
            ai_explanation                      TEXT,
            risk_notes                          TEXT,
            confidence_score                    NUMERIC,
            confidence_level                    VARCHAR(20),
            status                              VARCHAR(30) DEFAULT 'pending_user_approval',
            created_profile_id                  UUID        NULL,
            created_at                          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at                          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_sugg_status
        ON profile_suggestions (user_id, status, created_at DESC)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_sugg_score
        ON profile_suggestions (user_id, confidence_score DESC)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS profile_suggestions"))
