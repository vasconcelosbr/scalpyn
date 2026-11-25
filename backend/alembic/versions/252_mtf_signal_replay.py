"""Independent immutable signal replay.

Revision ID: 252_mtf_signal_replay
Revises: 251_pump_monitor_timing
"""
from alembic import op
import sqlalchemy as sa

revision = '252_mtf_signal_replay'
down_revision = '251_pump_monitor_timing'
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text('CREATE EXTENSION IF NOT EXISTS pgcrypto'))
    op.execute(sa.text('''CREATE TABLE mtf_replay_contexts (
        identity text PRIMARY KEY, user_id uuid NOT NULL REFERENCES users(id),
        watchlist_id text NOT NULL, symbol text NOT NULL, profile_id text,
        candle_at timestamptz NOT NULL, status text NOT NULL,
        armed jsonb NOT NULL DEFAULT '[]')'''))
    op.execute(sa.text('''CREATE TABLE mtf_signal_replays (
        id uuid PRIMARY KEY, context_id text NOT NULL REFERENCES mtf_replay_contexts(identity),
        user_id uuid NOT NULL REFERENCES users(id), watchlist_id text NOT NULL,
        symbol text NOT NULL, profile_id text, triggered_at timestamptz NOT NULL,
        captured_at timestamptz NOT NULL, baseline double precision NOT NULL CHECK(baseline>0),
        target double precision NOT NULL, stop double precision,
        snapshot jsonb NOT NULL, armed jsonb NOT NULL,
        UNIQUE(context_id,triggered_at))'''))
    op.execute(sa.text('CREATE INDEX ix_mtf_replay_owner ON mtf_signal_replays(user_id,triggered_at DESC,id DESC)'))
    op.execute(sa.text('''CREATE TABLE mtf_replay_progress (
        replay_id uuid PRIMARY KEY REFERENCES mtf_signal_replays(id),
        status text NOT NULL DEFAULT 'OPEN', next_at timestamptz NOT NULL,
        checked_at timestamptz, result jsonb, issue text)'''))
    op.execute(sa.text('''CREATE TABLE mtf_replay_candles (
        replay_id uuid NOT NULL REFERENCES mtf_signal_replays(id),
        time timestamptz NOT NULL, payload jsonb NOT NULL,
        PRIMARY KEY(replay_id,time))'''))
    op.execute(sa.text('''CREATE TABLE mtf_replay_snapshots (
        replay_id uuid NOT NULL REFERENCES mtf_signal_replays(id),
        identity text NOT NULL, captured_at timestamptz NOT NULL,
        payload jsonb NOT NULL, PRIMARY KEY(replay_id,identity))'''))
    for table in ('mtf_signal_replays', 'mtf_replay_candles', 'mtf_replay_snapshots'):
        op.execute(sa.text(f'''CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
            ON {table} FOR EACH ROW EXECUTE FUNCTION mtf_history_immutable()'''))


def downgrade():
    # Never destroy signal evidence on application rollback.
    pass
