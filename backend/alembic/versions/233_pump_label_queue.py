"""Pump-only indexed incremental horizon queue and bounded-job evidence."""
from alembic import op
import sqlalchemy as sa
revision="233_pump_label_queue"
down_revision="232_pump_exact_price_paths"
branch_labels=None
depends_on=None

def upgrade():
    statements="""
    CREATE TABLE IF NOT EXISTS pump_opportunity_label_queue(
        observation_id uuid NOT NULL REFERENCES pump_opportunity_observations(observation_id),
        user_id uuid NOT NULL,label_spec_hash varchar(64) NOT NULL,horizon_minutes integer NOT NULL,
        ready_at timestamptz NOT NULL,completed_at timestamptz,
        PRIMARY KEY(observation_id,label_spec_hash,horizon_minutes));
    CREATE INDEX IF NOT EXISTS ix_pump_label_due ON pump_opportunity_label_queue(user_id,ready_at,observation_id)
        WHERE completed_at IS NULL;
    CREATE TABLE IF NOT EXISTS pump_opportunity_job_runs(
        run_id uuid PRIMARY KEY,user_id uuid NOT NULL,finished_at timestamptz NOT NULL DEFAULT now(),payload jsonb NOT NULL);
    CREATE INDEX IF NOT EXISTS ix_pump_job_owner ON pump_opportunity_job_runs(user_id,finished_at DESC);
    INSERT INTO pump_opportunity_label_queue(observation_id,user_id,label_spec_hash,horizon_minutes,ready_at)
    SELECT o.observation_id,o.user_id,o.payload->'manifest'->>'label_spec_hash',h.value::integer,
        o.decision_at+make_interval(mins=>h.value::integer)
            +make_interval(secs=>(o.payload->'label_spec'->>'settle_seconds')::double precision)
    FROM pump_opportunity_observations o
    CROSS JOIN LATERAL jsonb_array_elements_text(o.payload->'label_spec'->'horizons_minutes') h(value)
    WHERE NOT EXISTS(SELECT 1 FROM pump_opportunity_labels l WHERE l.observation_id=o.observation_id
        AND l.label_spec_hash=o.payload->'manifest'->>'label_spec_hash' AND l.horizon_minutes=h.value::integer)
    ON CONFLICT DO NOTHING;
    """
    for statement in statements.split(";"):
        if statement.strip():op.execute(sa.text(statement))

def downgrade():
    pass  # preserve raw history, queue and runtime evidence
