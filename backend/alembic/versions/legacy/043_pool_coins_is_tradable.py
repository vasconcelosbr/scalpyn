"""Add pool_coins.is_tradable separating execution-gate from ingestion-gate.

Revision ID: 043_pool_coins_is_tradable
Revises: 042_trade_monitor_price_source
Create Date: 2026-05-06

Context (Task #232)
-------------------
``pool_coins.is_approved`` (migration 035) was overloaded to gate FOUR
disjoint domains simultaneously:

  1. "symbol monitored by collector"
  2. "symbol eligible for indicator computation"
  3. "symbol eligible for the L1/L2/L3 funnel entry"
  4. "symbol eligible for signal evaluation / live execution"

The first three are ingestion-domain (pre-pipeline) and naturally align
with ``pool_coins.is_active`` ("operator added this symbol to the pool").
Only the fourth is an EXECUTION-domain gate — operator authorising live
trading for a specific symbol.

This migration introduces ``is_tradable`` as the explicit execution gate.
The rest of the codebase migrates the ingestion-side filters from
``is_approved=true`` → ``is_active=true``, and the execution-side filters
(``evaluate_signals``, ``execute_buy``) from ``is_approved=true`` →
``is_active=true AND is_tradable=true``.

Backwards-compatibility (rolling deploy)
----------------------------------------
* ``is_approved`` is **not** dropped here — kept for one full deploy
  cycle so that any unmerged code path still reads it.
* A trigger ``pool_coins_is_approved_sync`` mirrors
  ``UPDATE ... SET is_approved`` into ``is_tradable`` (when the operator
  forgot to set tradable explicitly) so SQL-driven workflows from the
  pre-#232 era keep producing the new (more conservative) semantics.
* Trigger + drop of ``is_approved`` is scheduled for a later migration
  once production stabilises.

Index
-----
Partial index ``ix_pool_coins_tradable`` covers the hottest read on the
execution path (``WHERE is_active AND is_tradable``).

Critical-schema rule
--------------------
``is_tradable`` is **not** added to ``_critical_schema.py`` in this
deploy (rule N+1). It will be added in a follow-up after one full
deploy cycle without incident.
"""

from alembic import op
import sqlalchemy as sa


revision = "043_pool_coins_is_tradable"
down_revision = "042_trade_monitor_price_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Column with conservative default (false) — never auto-authorise.
    op.execute(sa.text("""
        ALTER TABLE pool_coins
            ADD COLUMN IF NOT EXISTS is_tradable BOOLEAN NOT NULL DEFAULT false
    """))

    # 2. Backfill: every previously-approved row keeps execution rights
    # so this migration is operationally a no-op for current behaviour.
    op.execute(sa.text("""
        UPDATE pool_coins
           SET is_tradable = TRUE
         WHERE is_tradable = FALSE
           AND is_approved = TRUE
    """))

    # 3. Hot-path partial index — the execution gate is queried per
    # evaluate_signals / execute_buy cycle (sub-second cadence).
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_pool_coins_tradable
            ON pool_coins (symbol, market_type)
         WHERE is_active = TRUE AND is_tradable = TRUE
    """))

    # 4. Sync trigger: when an operator runs
    #     UPDATE pool_coins SET is_approved = true WHERE ...
    # (legacy code path / SQL bookmark), mirror the change into
    # ``is_tradable`` UNLESS the same statement also explicitly set
    # ``is_tradable``. This preserves the looser "approve = trade-OK"
    # semantics from before Task #232 for ad-hoc operational SQL.
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION pool_coins_sync_is_tradable()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.is_approved IS DISTINCT FROM OLD.is_approved
               AND NEW.is_tradable = OLD.is_tradable THEN
                NEW.is_tradable := NEW.is_approved;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """))

    op.execute(sa.text("""
        DROP TRIGGER IF EXISTS pool_coins_is_approved_sync ON pool_coins
    """))
    op.execute(sa.text("""
        CREATE TRIGGER pool_coins_is_approved_sync
        BEFORE UPDATE ON pool_coins
        FOR EACH ROW
        EXECUTE FUNCTION pool_coins_sync_is_tradable()
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TRIGGER IF EXISTS pool_coins_is_approved_sync ON pool_coins"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS pool_coins_sync_is_tradable()"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_pool_coins_tradable"))
    op.execute(sa.text("ALTER TABLE pool_coins DROP COLUMN IF EXISTS is_tradable"))
