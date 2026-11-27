"""Bound latest profile decision reads to indexed per-symbol probes.

Revision ID: 254_profile_latest_index
Revises: 253_mtf_pump_flow_revisions
"""
from alembic import op

revision = '254_profile_latest_index'
down_revision = '253_mtf_pump_flow_revisions'
branch_labels = None
depends_on = None

INDEX_SQL = '''CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_decisions_profile_symbol_latest
    ON decisions_log (user_id, profile_id, symbol, created_at DESC, id DESC)
    WHERE event_type NOT IN ('PROFILE_CONSOLIDATION', 'PROFILE_CONSOLIDATION_REJECTED')'''


def upgrade():
    # Pre-build concurrently on the hot table before release. No JSONB payload
    # is stored in the index. Refuse an invalid leftover from an interrupted build.
    with op.get_context().autocommit_block():
        op.execute(INDEX_SQL)
        op.execute('''DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_index
                WHERE indexrelid = 'ix_decisions_profile_symbol_latest'::regclass
                AND indisvalid AND indisready) THEN
                RAISE EXCEPTION 'ix_decisions_profile_symbol_latest is invalid; rebuild before release';
            END IF;
        END $$''')


def downgrade():
    with op.get_context().autocommit_block():
        op.execute('DROP INDEX CONCURRENTLY IF EXISTS ix_decisions_profile_symbol_latest')
