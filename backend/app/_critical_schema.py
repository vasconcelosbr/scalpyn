"""Single source of truth for the production critical-column list.

Two consumers must check the exact same set of (table, column) pairs:

* ``app.main.health_check_schema`` — runtime health probe at
  ``GET /api/health/schema`` (returns 503 + missing list if any are absent).
* ``scripts.check_critical_schema`` — boot-time gate invoked by ``start.sh``
  after the ``alembic stamp head`` fallback (exit 1 on drift so Cloud Run
  rolls back the revision).

Keeping the list in this tiny zero-dependency module means the boot gate
stays decoupled from the FastAPI app graph (no slow imports, no side effects)
while still sharing the exact list with the health endpoint — eliminating
the silent-divergence risk that two literal lists in two files would carry.

DO NOT add imports from anything in ``app.*`` here.  This module must remain
importable from a stripped-down boot context (no SQLAlchemy engine, no
config, no Sentry, no logging configured yet).
"""

from __future__ import annotations

from typing import List, Tuple

# Critical (table, column) pairs — every column declared by an ORM model
# whose absence would 500 a user-facing endpoint or break a scheduler loop.
# Keep in sync with backend/alembic/versions/021_init_db_parity_catchall.py
# and onwards.  Adding a new column?  Add it here AND ship the alembic
# migration — see replit.md "Schema Bootstrap" section.
CRITICAL_COLUMNS: List[Tuple[str, str]] = [
    ("pools", "overrides"),
    ("pools", "autopilot_enabled"),
    ("pipeline_watchlists", "market_mode"),
    ("pipeline_watchlists", "last_scanned_at"),
    ("pipeline_watchlist_assets", "execution_id"),
    ("pipeline_watchlist_assets", "score_long"),
    ("pipeline_watchlist_assets", "score_short"),
    ("pipeline_watchlist_assets", "confidence_score"),
    ("pipeline_watchlist_assets", "futures_direction"),
    ("pipeline_watchlist_assets", "entry_long_blocked"),
    ("pipeline_watchlist_assets", "entry_short_blocked"),
    ("pipeline_watchlist_assets", "refreshed_at"),
    ("pipeline_watchlist_assets", "analysis_snapshot"),
    ("pipeline_watchlist_rejections", "execution_id"),
    ("pipeline_watchlist_rejections", "analysis_snapshot"),
    ("watchlist_profiles", "profile_type"),
    ("trades", "exchange_order_id"),
    ("trades", "source"),
    # Added by migration 026; without these, _persist_decision_logs raises
    # UndefinedColumnError and poisons the session for the whole scan loop.
    ("decisions_log", "direction"),
    ("decisions_log", "event_type"),
    # Added by migration 032; without it, both structural and microstructure
    # schedulers fail every cycle (~30k UndefinedColumnError / day) and
    # cascade into InFailedSQLTransactionError + QueuePool exhaustion.
    # Detection here is what start.sh's post-stamp probe relies on as well.
    ("indicators", "scheduler_group"),
    # Added by migration 033; indicator writers now insert this unconditionally
    # and futures queries filter on it, so missing it would break writes and
    # silently collapse spot/futures isolation.
    ("indicators", "market_type"),
    # Added by migration 035; without this, get_approved_pool_symbols raises
    # ProgrammingError and all symbol collections fail with zero ohlcv inserts.
    ("pool_coins", "is_approved"),
]
