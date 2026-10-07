"""Index Pump opportunity observations by (owner, decision_at).

The daily Pump ML selection walks candidates with ``ORDER BY decision_at`` per
owner; only ``(user_id, slot_at)`` was indexed, so every cursor sorted the whole
owner history and the read hit the statement timeout (``QueryCanceledError`` on
2026-10-03 and 2026-10-07 in ``pump_ml_job_runs``). Additive, built concurrently.

Revision ID: 237_pump_obs_decision_idx
Revises: 236_pump_capital_flow
"""
from alembic import op

revision = "237_pump_obs_decision_idx"
down_revision = "236_pump_capital_flow"
branch_labels = None
depends_on = None


def upgrade():
    # PostgreSQL rejects CREATE INDEX CONCURRENTLY inside a transaction.
    with op.get_context().autocommit_block():
        op.execute("""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_pump_opportunity_owner_decision
                ON pump_opportunity_observations (user_id, decision_at DESC, observation_id)
        """)


def downgrade():
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_pump_opportunity_owner_decision")
