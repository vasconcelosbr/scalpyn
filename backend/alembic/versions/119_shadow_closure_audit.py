"""Shadow trade closure audit table.

Tracks every shadow trade closure performed by the fast-barrier-scan
path (and backfill script). Provides idempotent audit trail with
closure reason, price source, and run ID for debugging.

Revision ID: 119_shadow_closure_audit
Revises: 118_ai_review_analysis_context
Create Date: 2026-06-28
"""

from alembic import op
import sqlalchemy as sa

revision = "119_shadow_closure_audit"
down_revision = "118_ai_review_analysis_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS shadow_trade_closure_audit (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            shadow_trade_id uuid NOT NULL,
            source text,
            symbol text,
            previous_status text,
            entry_price numeric,
            exit_price numeric,
            tp_price numeric,
            sl_price numeric,
            pnl_pct numeric,
            pnl_usdt numeric,
            closure_reason text NOT NULL,
            price_source text,
            price_timestamp timestamptz,
            price_age_seconds int,
            closer_run_id uuid,
            payload jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_stca_shadow_trade_id "
        "ON shadow_trade_closure_audit (shadow_trade_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_stca_source_reason "
        "ON shadow_trade_closure_audit (source, closure_reason)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_stca_created_at "
        "ON shadow_trade_closure_audit (created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_stca_closer_run_id "
        "ON shadow_trade_closure_audit (closer_run_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS shadow_trade_closure_audit")
