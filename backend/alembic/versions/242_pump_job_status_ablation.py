"""Allow the 'ablation' status in pump_ml_job_runs (v1.18.2).

The feature-group ablation (family candle_ablation) finishes with status
'ablation', which the pump_job_status CHECK (migration 234) rejected: the final
UPDATE failed and the run stayed 'running' until its deadline although every
variant had been computed and saved. Constraint widened; nothing else changes.

Revision ID: 242_pump_job_status_ablation
Revises: 241_pump_book_5m
"""
from alembic import op
import sqlalchemy as sa

revision = "242_pump_job_status_ablation"
down_revision = "241_pump_book_5m"
branch_labels = None
depends_on = None

STATUSES = ("running", "blocked", "challenger", "failed", "ablation")


def upgrade():
    op.execute(sa.text("ALTER TABLE pump_ml_job_runs DROP CONSTRAINT IF EXISTS pump_job_status"))
    op.execute(sa.text("ALTER TABLE pump_ml_job_runs ADD CONSTRAINT pump_job_status CHECK (status IN ("
                       + ",".join(f"'{s}'" for s in STATUSES) + "))"))


def downgrade():
    op.execute(sa.text("UPDATE pump_ml_job_runs SET status='failed' WHERE status='ablation'"))
    op.execute(sa.text("ALTER TABLE pump_ml_job_runs DROP CONSTRAINT IF EXISTS pump_job_status"))
    op.execute(sa.text("ALTER TABLE pump_ml_job_runs ADD CONSTRAINT pump_job_status "
                       "CHECK (status IN ('running','blocked','challenger','failed'))"))
