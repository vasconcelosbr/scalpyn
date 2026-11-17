"""Immutable observation inputs and separate future labels.

Revision ID: 248_mtf_history
Revises: 247_mtf_chart
"""
import json
from alembic import op
import sqlalchemy as sa

revision = '248_mtf_history'
down_revision = '247_mtf_chart'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS pgcrypto'))
    op.execute(sa.text('''CREATE TABLE mtf_history_capture_jobs (
        identity text PRIMARY KEY,user_id uuid NOT NULL REFERENCES users(id),watchlist_id text NOT NULL,
        member jsonb NOT NULL,scheduled_at timestamptz NOT NULL,dispatched_at timestamptz,
        status text NOT NULL DEFAULT 'PENDING',attempts integer NOT NULL DEFAULT 0,error text)'''))
    op.execute(sa.text("CREATE INDEX ix_mtf_capture_pending ON mtf_history_capture_jobs(scheduled_at) WHERE status='PENDING'"))
    op.execute(sa.text('CREATE INDEX ix_mtf_capture_owner ON mtf_history_capture_jobs(user_id,status)'))
    op.execute(sa.text('''CREATE TABLE mtf_history_observations (
        id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id),
        watchlist_id text NOT NULL, symbol text NOT NULL, profile_id text,
        identity text NOT NULL UNIQUE, observed_at timestamptz NOT NULL,
        status text NOT NULL, trend text, quality text NOT NULL,
        payload jsonb NOT NULL,
        CHECK (payload->>'operational_effect' = 'false'))'''))
    op.execute(sa.text('CREATE INDEX ix_mtf_history_owner ON mtf_history_observations(user_id,observed_at DESC,id DESC)'))
    op.execute(sa.text('CREATE INDEX ix_mtf_history_symbol ON mtf_history_observations(user_id,watchlist_id,symbol,observed_at,id)'))
    op.execute(sa.text('''CREATE TABLE mtf_history_outcomes (
        observation_id uuid NOT NULL REFERENCES mtf_history_observations(id),
        horizon_minutes integer NOT NULL CHECK (horizon_minutes > 0),
        due_at timestamptz NOT NULL, status text NOT NULL DEFAULT 'PENDING',
        payload jsonb, updated_at timestamptz,
        PRIMARY KEY(observation_id,horizon_minutes))'''))
    op.execute(sa.text("CREATE INDEX ix_mtf_history_due ON mtf_history_outcomes(due_at) WHERE status = 'PENDING'"))
    op.execute(sa.text('''CREATE TABLE mtf_history_exports (
        id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        request jsonb NOT NULL, status text NOT NULL DEFAULT 'PENDING',
        content bytea, row_count bigint, error text, completed_at timestamptz)'''))
    # Enforce append-only inputs even when a future caller attempts an UPDATE.
    op.execute(sa.text("""CREATE FUNCTION mtf_history_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'MTF_HISTORY_IMMUTABLE'; END $$"""))
    op.execute(sa.text('''CREATE TRIGGER mtf_history_no_rewrite BEFORE UPDATE OR DELETE
        ON mtf_history_observations FOR EACH ROW EXECUTE FUNCTION mtf_history_immutable()'''))
    op.execute(sa.text('''INSERT INTO config_profiles(id,user_id,config_type,config_json,is_active,created_at,updated_at)
        SELECT gen_random_uuid(),u.id,'mtf_history',CAST(:config AS jsonb),true,now(),now()
        FROM users u WHERE NOT EXISTS (SELECT 1 FROM config_profiles c
        WHERE c.user_id=u.id AND c.config_type='mtf_history' AND c.pool_id IS NULL)''').bindparams(
        config=json.dumps({'enabled': True, 'horizons_minutes': [15,60,240,1440]})))


def downgrade():
    # Rollbacks preserve captured evidence. Disable future capture via settings.
    pass
