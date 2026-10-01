"""Pump-only explicit resource block, preserving pending labels.

Revises: 234_pump_ml_job_ledger
"""
from alembic import op
import sqlalchemy as sa

revision="235_pump_label_resource_block"
down_revision="234_pump_ml_job_ledger"
branch_labels=None
depends_on=None

def upgrade():
    op.execute(sa.text("ALTER TABLE pump_opportunity_label_queue ADD COLUMN IF NOT EXISTS resource_block jsonb"))

def downgrade():
    pass  # Preserve explicit resource evidence and pending identities.
