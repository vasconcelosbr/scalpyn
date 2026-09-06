"""Shadow continuation evidence, independent of historical labels.

Revision ID: 219_shadow_l3_continuation
Revises: 218_mtf_profile_activation_audit
"""
from alembic import op
import sqlalchemy as sa

revision = "219_shadow_l3_continuation"
down_revision = "218_mtf_profile_activation_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_l3_flow_trades (
            exchange TEXT NOT NULL, market_type TEXT NOT NULL, symbol TEXT NOT NULL,
            trade_id TEXT NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
            available_at TIMESTAMPTZ NOT NULL, persisted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            side TEXT NOT NULL CHECK (side IN ('buy','sell')),
            amount NUMERIC NOT NULL CHECK (amount > 0), unit TEXT NOT NULL DEFAULT 'BASE',
            PRIMARY KEY(exchange, market_type, symbol, trade_id)
        )
    """))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_shadow_l3_flow_time ON shadow_l3_flow_trades(exchange,market_type,symbol,occurred_at)"))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_l3_exit_states (
            shadow_id UUID PRIMARY KEY REFERENCES shadow_trades(id) ON DELETE RESTRICT,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            policy_hash TEXT NOT NULL, policy JSONB NOT NULL, state JSONB NOT NULL DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            checked_at TIMESTAMPTZ
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_l3_exit_decisions (
            shadow_id UUID NOT NULL REFERENCES shadow_trades(id) ON DELETE RESTRICT,
            candle_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ NOT NULL,
            policy_hash TEXT NOT NULL, evidence JSONB NOT NULL, state JSONB NOT NULL,
            PRIMARY KEY(shadow_id, candle_at)
        )
    """))
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS shadow_l3_policy_validations (
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            policy_hash TEXT NOT NULL, report JSONB NOT NULL,
            approved_at TIMESTAMPTZ, approved_by UUID REFERENCES users(id),
            PRIMARY KEY(user_id, policy_hash)
        )
    """))


def downgrade():
    # Never drop captured evidence or orphan an open version on downgrade.
    raise RuntimeError("Forward-only evidence migration; disable new admissions instead")
