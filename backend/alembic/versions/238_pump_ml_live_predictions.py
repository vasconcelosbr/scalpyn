"""Pump ML live predictions (Pump Score v1.14).

Additive only: one append-only table with the probability the APPLIED model gave
each pool asset at each closed candle. Lets the live AUC be compared with the
walk-forward numbers. Nothing existing changes.

Revision ID: 238_pump_ml_live_predictions
Revises: 237_pump_obs_decision_idx
"""
from alembic import op
import sqlalchemy as sa

revision = "238_pump_ml_live_predictions"
down_revision = "237_pump_obs_decision_idx"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS pump_ml_live_predictions (
            user_id uuid NOT NULL,
            experiment_id text NOT NULL,
            objective varchar(48) NOT NULL,
            horizon_minutes integer NOT NULL,
            decision_at timestamptz NOT NULL,
            symbol varchar(40) NOT NULL,
            p_up double precision NOT NULL,
            applied boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, experiment_id, horizon_minutes, decision_at, symbol)
        )
    """))
    op.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_pump_ml_live_predictions_user_decision "
        "ON pump_ml_live_predictions (user_id, objective, horizon_minutes, decision_at)"))


def downgrade():
    op.execute(sa.text("DROP TABLE IF EXISTS pump_ml_live_predictions"))
