"""AI Critic analysis context: audit trail for source, period, filters, sample.

Revision ID: 116_ai_review_analysis_context
Revises: 115_autopilot_shadow_calibration
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa


revision = "116_ai_review_analysis_context"
down_revision = "115_autopilot_shadow_calibration"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE profile_ai_reviews
        ADD COLUMN IF NOT EXISTS analysis_context jsonb
    """)

    op.execute("""
        ALTER TABLE profile_ai_reviews
        ADD COLUMN IF NOT EXISTS context_payload_hash text
    """)

    op.execute("""
        ALTER TABLE profile_ai_reviews
        ADD COLUMN IF NOT EXISTS context_query_hash text
    """)

    # Mark all existing reviews that have tokens but no context as legacy
    op.execute("""
        UPDATE profile_ai_reviews
        SET analysis_context = '{"_legacy": true, "note": "review created before analysis_context was tracked"}'::jsonb
        WHERE analysis_context IS NULL
          AND status = 'COMPLETED'
          AND tokens_input > 0
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE profile_ai_reviews DROP COLUMN IF EXISTS context_query_hash")
    op.execute("ALTER TABLE profile_ai_reviews DROP COLUMN IF EXISTS context_payload_hash")
    op.execute("ALTER TABLE profile_ai_reviews DROP COLUMN IF EXISTS analysis_context")
