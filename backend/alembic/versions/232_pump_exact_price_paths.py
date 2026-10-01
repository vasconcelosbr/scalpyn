"""Archive exact Pump-only price paths with coverage revisions.

Revision ID: 232_pump_exact_price_paths
Revises: 231_pump_opportunity_v2
"""
from alembic import op
import sqlalchemy as sa

revision="232_pump_exact_price_paths"
down_revision="231_pump_opportunity_v2"
branch_labels=None
depends_on=None


def upgrade():
    op.execute(sa.text("""CREATE TABLE IF NOT EXISTS pump_opportunity_price_paths(
        user_id uuid NOT NULL,instrument_id uuid NOT NULL,symbol varchar(40) NOT NULL,
        bucket_start timestamptz NOT NULL,content_hash varchar(64) NOT NULL,
        captured_at timestamptz NOT NULL DEFAULT now(),complete boolean NOT NULL,payload jsonb NOT NULL,
        PRIMARY KEY(user_id,instrument_id,bucket_start,content_hash))"""))
    op.execute(sa.text("""CREATE INDEX IF NOT EXISTS ix_pump_price_paths_owner_instrument
        ON pump_opportunity_price_paths(user_id,instrument_id,bucket_start,complete DESC)"""))


def downgrade():
    pass
