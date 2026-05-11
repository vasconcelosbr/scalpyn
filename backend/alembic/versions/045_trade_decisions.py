"""Add trade_decisions table — full audit of every pipeline decision.

Revision ID: 045_trade_decisions
Revises: 044_executions_lifecycle
Create Date: 2026-05-11

Context
-------
Today the pipeline produces decisions in several places (L1 filter, L2
score, L3 robust, EXECUTION gate) and each writes its own partial trail
(``decisions_log``, ``trade_tracking``, ``pipeline_watchlist_assets``).
None of them captures the *full* "why" of a decision — the rule that
blocked it, the indicator snapshot at the moment, the per-stage
latencies, etc.

``trade_decisions`` is the single, append-only audit log:

  * one row per decision (any status: APPROVED / REJECTED / BLOCKED /
    SKIPPED), at any stage (L1 / L2 / L3 / EXECUTION);
  * carries ``trace_id`` so a single end-to-end flow can be reconstructed
    by joining all rows for that trace;
  * structured ``rule_details`` / ``rules_*`` JSONB so the front-end can
    explain exactly which rule fired and with which value/threshold;
  * optional ``trade_id`` FK back to ``trades`` when (and only when) the
    decision led to an actual trade being created.

Rule N/N+1
----------
This table is NOT added to ``_critical_schema.py`` here. It is added in
deploy N+1 once production proves the columns exist (see Skill #7 in
the alembic-migration-guardrails).
"""

from alembic import op
import sqlalchemy as sa


revision = "045_trade_decisions"
down_revision = "044_executions_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS trade_decisions (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            trace_id             VARCHAR(64) NOT NULL,
            user_id              UUID NULL REFERENCES users(id),
            pool_id              UUID NULL REFERENCES pools(id),
            symbol               VARCHAR(20) NOT NULL,
            market_type          VARCHAR(10) NOT NULL,
            exchange             VARCHAR(50) NULL,
            decided_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status               VARCHAR(20) NOT NULL,
            stage                VARCHAR(10) NOT NULL,
            reason               TEXT        NULL,
            blocking_rule        VARCHAR(255) NULL,
            rule_details         JSONB       NULL,
            rules_matched        JSONB       NULL,
            rules_failed         JSONB       NULL,
            rules_skipped        JSONB       NULL,
            score_breakdown      JSONB       NULL,
            indicators_snapshot  JSONB       NULL,
            latency_ms           JSONB       NULL,
            trade_id             UUID        NULL REFERENCES trades(id)
        )
    """))

    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_trade_decisions_user_time
            ON trade_decisions (user_id, decided_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_trade_decisions_symbol_time
            ON trade_decisions (symbol, decided_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_trade_decisions_status_time
            ON trade_decisions (status, decided_at DESC)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_trade_decisions_trace
            ON trade_decisions (trace_id)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS trade_decisions"))
