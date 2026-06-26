"""Catch-all migration: add all columns that may be missing from pre-migration databases

Revision ID: 005_catch_all_missing_columns
Revises: 004_pool_coins_discovery_fields
Create Date: 2026-03-22

Context
-------
This project originally used SQLAlchemy create_all() + manual ALTER TABLE
in init_db.py for schema management. Alembic was introduced later.

Databases bootstrapped by create_all from an older model version may be
missing columns that were added to models without a corresponding migration.

This migration uses only raw SQL with IF NOT EXISTS / DO $$ blocks so it
is safe to run against ANY database state:
  - Fresh DB (create_all just ran, all columns present)  → no-op
  - Old production DB (missing columns)                  → columns added
  - DB managed by previous partial migrations            → any gaps filled

Tables covered
--------------
pools:
  - description   TEXT
  - is_active     BOOLEAN DEFAULT TRUE
  - mode          VARCHAR(20) DEFAULT 'paper'
  - market_type   VARCHAR(20) DEFAULT 'spot'
  - profile_id    UUID (FK to profiles, nullable)
  - overrides     JSONB DEFAULT '{}'
  - updated_at    TIMESTAMPTZ DEFAULT NOW()

pool_coins:
  - market_type   VARCHAR(10) DEFAULT 'spot'
  - is_active     BOOLEAN DEFAULT TRUE
  - added_at      TIMESTAMPTZ DEFAULT NOW()
  - origin        VARCHAR(20) DEFAULT 'manual'
  - discovered_at TIMESTAMPTZ

trades:
  - pool_id       UUID (FK to pools, nullable)  — in model but never migrated
  - direction     VARCHAR(10)                   — in model but never migrated
"""

from alembic import op
import sqlalchemy as sa


# ── helpers ──────────────────────────────────────────────────────────────────

def _add_if_missing(table: str, column: str, definition: str) -> None:
    """ALTER TABLE … ADD COLUMN IF NOT EXISTS."""
    op.execute(sa.text(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition};"
    ))


# ─────────────────────────────────────────────────────────────────────────────

revision = "005_catch_all_missing_columns"
down_revision = "004_pool_coins_discovery_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── pools ─────────────────────────────────────────────────────────────────
    _add_if_missing("pools", "description",  "TEXT")
    _add_if_missing("pools", "is_active",    "BOOLEAN DEFAULT TRUE")
    _add_if_missing("pools", "mode",         "VARCHAR(20) DEFAULT 'paper'")
    _add_if_missing("pools", "market_type",  "VARCHAR(20) DEFAULT 'spot'")
    _add_if_missing("pools", "overrides",    "JSONB DEFAULT '{}'")
    _add_if_missing("pools", "updated_at",   "TIMESTAMPTZ DEFAULT NOW()")

    # profile_id has a FK; add plain column first, then constraint separately
    # so that IF the column already exists we don't error on the FK either.
    _add_if_missing("pools", "profile_id", "UUID")
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'pools_profile_id_fkey'
            ) THEN
                ALTER TABLE pools
                ADD CONSTRAINT pools_profile_id_fkey
                FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE SET NULL;
            END IF;
        END
        $$;
    """))

    # ── pool_coins ────────────────────────────────────────────────────────────
    _add_if_missing("pool_coins", "market_type",   "VARCHAR(10) DEFAULT 'spot'")
    _add_if_missing("pool_coins", "is_active",     "BOOLEAN DEFAULT TRUE")
    _add_if_missing("pool_coins", "added_at",      "TIMESTAMPTZ DEFAULT NOW()")
    _add_if_missing("pool_coins", "origin",        "VARCHAR(20) DEFAULT 'manual'")
    _add_if_missing("pool_coins", "discovered_at", "TIMESTAMPTZ")

    # ── trades ────────────────────────────────────────────────────────────────
    # pool_id and direction were added to the Trade model but no prior migration
    # covered them; create_all may or may not have included them depending on
    # when the table was first created.
    _add_if_missing("trades", "direction", "VARCHAR(10)")
    _add_if_missing("trades", "pool_id",   "UUID")
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'trades_pool_id_fkey'
            ) THEN
                ALTER TABLE trades
                ADD CONSTRAINT trades_pool_id_fkey
                FOREIGN KEY (pool_id) REFERENCES pools(id);
            END IF;
        END
        $$;
    """))


def downgrade() -> None:
    # Intentionally left minimal — this is a safety-net migration.
    # Dropping these columns in a production rollback would be destructive.
    pass
