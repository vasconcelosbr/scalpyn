"""set activated_at for v52 (was NULL after direct-DB promotion)

Revision ID: c001v52activ
Revises: b2780092b9ca
Create Date: 2026-06-30

v52 (L1_SPECTRUM LightGBM) was promoted from candidate→active via direct DB
UPDATE (commit 66319db) which did not set activated_at. The gcs_model_loader
query uses ORDER BY activated_at DESC NULLS LAST so v52 is still served, but
activated_at=NULL breaks audit/visibility. This migration backfills it with
the model's own created_at timestamp.
"""

from alembic import op


revision = "c001v52activ"
down_revision = "b2780092b9ca"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE ml_models
           SET activated_at = created_at
         WHERE version = '52'
           AND status = 'active'
           AND activated_at IS NULL
    """)


def downgrade() -> None:
    op.execute("""
        UPDATE ml_models
           SET activated_at = NULL
         WHERE version = '52'
           AND activated_at = created_at
    """)
