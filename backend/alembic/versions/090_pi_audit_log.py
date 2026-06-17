"""profile_intelligence_audit_log — immutable event log for PI Engine actions.

Revision ID: 090_pi_audit_log
Revises: 089_pi_suggestions
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "090_pi_audit_log"
down_revision = "089_pi_suggestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS profile_intelligence_audit_log (
            id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             UUID        NOT NULL,
            run_id              UUID        NULL,
            suggestion_id       UUID        NULL,
            combination_id      UUID        NULL,
            event_type          VARCHAR(60) NOT NULL,
            event_description   TEXT,
            payload_json        JSONB,
            result_json         JSONB,
            model_provider      VARCHAR(30),
            model_name          VARCHAR(60),
            prompt_text         TEXT,
            response_text       TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_audit_user
        ON profile_intelligence_audit_log (user_id, created_at DESC)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_audit_run
        ON profile_intelligence_audit_log (run_id)
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS idx_pi_audit_sugg
        ON profile_intelligence_audit_log (suggestion_id)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS profile_intelligence_audit_log"))
