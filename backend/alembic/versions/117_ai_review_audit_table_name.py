"""Align AI review reclassification audit table with the operational contract.

Revision ID: 117_ai_review_audit_name
Revises: 116_ai_review_safety
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa

revision = "117_ai_review_audit_name"
down_revision = "116_ai_review_safety"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE IF EXISTS profile_ai_review_reclassification_audit
        RENAME TO profile_ai_reviews_reclassification_audit
    """))


def downgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE IF EXISTS profile_ai_reviews_reclassification_audit
        RENAME TO profile_ai_review_reclassification_audit
    """))