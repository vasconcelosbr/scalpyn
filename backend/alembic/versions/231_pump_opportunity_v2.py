"""Add isolated Pump opportunity contracts.

Revision ID: 231_pump_opportunity_v2
Revises: 230_pump_research_dataset
"""
from alembic import op
import sqlalchemy as sa

revision = "231_pump_opportunity_v2"
down_revision = "230_pump_research_dataset"
branch_labels = None
depends_on = None


def upgrade():
    statements="""
        CREATE TABLE IF NOT EXISTS pump_opportunity_observations (
            observation_id uuid PRIMARY KEY, user_id uuid NOT NULL,
            instrument_id uuid NOT NULL, episode_id uuid NOT NULL, symbol varchar(40) NOT NULL,
            slot_at timestamptz NOT NULL, decision_at timestamptz NOT NULL,
            published_at timestamptz NOT NULL DEFAULT now(), contract_hash varchar(64) NOT NULL,
            payload jsonb NOT NULL,
            UNIQUE(user_id,instrument_id,slot_at)
        );
        CREATE INDEX IF NOT EXISTS ix_pump_opportunity_owner_slot
            ON pump_opportunity_observations(user_id,slot_at DESC,observation_id);
        CREATE INDEX IF NOT EXISTS ix_pump_opportunity_owner_episode
            ON pump_opportunity_observations(user_id,episode_id,decision_at);
        CREATE TABLE IF NOT EXISTS pump_opportunity_labels (
            observation_id uuid NOT NULL REFERENCES pump_opportunity_observations(observation_id),
            label_spec_hash varchar(64) NOT NULL, horizon_minutes integer NOT NULL,
            labeled_at timestamptz NOT NULL DEFAULT now(), payload jsonb NOT NULL,
            PRIMARY KEY(observation_id,label_spec_hash,horizon_minutes)
        );
        CREATE TABLE IF NOT EXISTS pump_ml_experiments (
            experiment_id uuid PRIMARY KEY, user_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(), manifest jsonb NOT NULL,
            metrics jsonb NOT NULL, artifact_namespace text NOT NULL,
            status text NOT NULL DEFAULT 'challenger',
            CONSTRAINT pump_ml_no_auto_promotion CHECK (status IN ('challenger','rejected','validating')),
            CONSTRAINT pump_ml_artifact_isolation CHECK (artifact_namespace LIKE 'pump_ml/%')
        );
        CREATE TABLE IF NOT EXISTS pump_ml_predictions (
            prediction_id uuid PRIMARY KEY, user_id uuid NOT NULL,
            observation_id uuid NOT NULL REFERENCES pump_opportunity_observations(observation_id),
            experiment_id uuid NOT NULL REFERENCES pump_ml_experiments(experiment_id),
            produced_at timestamptz NOT NULL DEFAULT now(), payload jsonb NOT NULL,
            applied_delta double precision NOT NULL DEFAULT 0 CHECK(applied_delta = 0)
        );
    """
    for statement in statements.split(";"):
        if statement.strip():
            op.execute(sa.text(statement))


def downgrade():
    # Intentional: rollback consumers/flags, preserve raw observations and artifacts.
    pass
