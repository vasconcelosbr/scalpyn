"""Point-in-time spot taker-flow snapshots for the Pump ML (v1.20).

``pump_flow_5m`` is upserted (late minute buckets revise a bucket after it closed),
so a training row read from it could see flow that did not exist at the decision.
``pump_flow_asof`` records, ONCE per decision time (close of a 5-minute candle),
what ``flow_buckets_1m`` held at that moment: per symbol and window, the sum of
taker buy/sell quote volume over the usable (non-partial) minutes, how many minutes
were usable / present, and ``computed_at`` (the availability time). Rows are
immutable: inserts use ON CONFLICT DO NOTHING and a trigger rejects UPDATE.
Training and live inference read the same rows.

Revision ID: 243_pump_flow_asof
Revises: 242_pump_job_status_ablation
"""
from alembic import op
import sqlalchemy as sa

revision = "243_pump_flow_asof"
down_revision = "242_pump_job_status_ablation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pump_flow_asof (
            symbol varchar(40) NOT NULL,
            decision_at timestamptz NOT NULL,
            window_minutes smallint NOT NULL CHECK (window_minutes > 0),
            buy_quote double precision,
            sell_quote double precision,
            trade_count bigint,
            usable_minutes smallint NOT NULL,
            present_minutes smallint NOT NULL,
            computed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, decision_at, window_minutes)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_pump_flow_asof_decision ON pump_flow_asof (decision_at)"))
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION pump_flow_asof_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'pump_flow_asof rows are immutable (point-in-time snapshots)';
        END;
        $$ LANGUAGE plpgsql
    """))
    op.execute(sa.text("DROP TRIGGER IF EXISTS pump_flow_asof_no_update ON pump_flow_asof"))
    op.execute(sa.text("""
        CREATE TRIGGER pump_flow_asof_no_update BEFORE UPDATE ON pump_flow_asof
        FOR EACH ROW EXECUTE FUNCTION pump_flow_asof_immutable()
    """))


def downgrade():
    op.execute(sa.text("DROP TABLE IF EXISTS pump_flow_asof"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS pump_flow_asof_immutable()"))
