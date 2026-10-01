"""Isolated Pump training run and artifact ledger."""
from alembic import op
import sqlalchemy as sa
revision="234_pump_ml_job_ledger"
down_revision="233_pump_label_queue"
branch_labels=None
depends_on=None
def upgrade():
    for sql in ("""CREATE TABLE IF NOT EXISTS pump_ml_job_runs(
        run_id uuid PRIMARY KEY,user_id uuid NOT NULL,started_at timestamptz NOT NULL DEFAULT now(),
        deadline_at timestamptz NOT NULL,finished_at timestamptz,status text NOT NULL,
        payload jsonb NOT NULL,CONSTRAINT pump_job_status CHECK(status IN('running','blocked','challenger','failed')))""",
        "CREATE INDEX IF NOT EXISTS ix_pump_ml_run_owner ON pump_ml_job_runs(user_id,started_at DESC)",
        """CREATE TABLE IF NOT EXISTS pump_ml_artifacts(
        experiment_id uuid NOT NULL REFERENCES pump_ml_experiments(experiment_id),
        path text NOT NULL,sha256 varchar(64) NOT NULL,content bytea NOT NULL,
        PRIMARY KEY(experiment_id,path),CONSTRAINT pump_artifact_namespace CHECK(path LIKE 'pump_ml/%'),
        CONSTRAINT pump_artifact_size CHECK(octet_length(content)<=5000000))"""):
        op.execute(sa.text(sql))
def downgrade():pass
