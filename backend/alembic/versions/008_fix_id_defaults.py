"""Fix: add gen_random_uuid() DEFAULT to UUID primary key columns

Revision ID: 008_fix_id_defaults
Revises: 007_ai_provider_keys
Create Date: 2026-03-22

Fixes NotNullViolationError when inserting rows without an explicit id.

Root cause: SQLAlchemy models use default=uuid.uuid4 (Python-side), but
the actual DB columns have no server-side DEFAULT. Raw SQL INSERTs that
omit the id column (e.g., in migration 006) fail with:
  ERROR: null value in column "id" violates not-null constraint

This migration:
  1. Enables pgcrypto (required for gen_random_uuid())
  2. Sets DEFAULT gen_random_uuid() on id columns of all main tables
     that have UUID PKs — safe to run multiple times (checks first)
  3. Re-runs the filters insert from migration 006 in case it failed

All operations use DO $$ blocks to be fully idempotent.
"""

from alembic import op
import sqlalchemy as sa

revision = "008_fix_id_defaults"
down_revision = "007_ai_provider_keys"
branch_labels = None
depends_on = None

# Tables with UUID PKs that may be missing server-side DEFAULT
_UUID_PK_TABLES = [
    "users",
    "config_profiles",
    "pools",
    "pool_coins",
    "trades",
    "orders",
    "exchange_connections",
    "profiles",
    "watchlist_profiles",
    "custom_watchlists",
    "pipeline_watchlists",
    "pipeline_watchlist_assets",
    "ai_provider_keys",
    "notification_settings",
]

_DEFAULT_FILTERS = """{
  "enabled": true,
  "logic": "AND",
  "filters": [
    {"id": "f_min_volume", "name": "Minimum 24h Volume",     "enabled": true, "indicator": "volume_24h",  "operator": ">=", "value": 1000000},
    {"id": "f_min_adx",    "name": "Minimum Trend Strength", "enabled": true, "indicator": "adx",         "operator": ">=", "value": 20},
    {"id": "f_max_spread", "name": "Maximum Spread %",       "enabled": true, "indicator": "spread_pct",  "operator": "<=", "value": 0.5}
  ]
}"""


def upgrade() -> None:
    # 1. Enable pgcrypto
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto;"))

    # 2. Add DEFAULT gen_random_uuid() to each UUID PK that lacks one
    for table in _UUID_PK_TABLES:
        op.execute(sa.text(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = '{table}'
                ) THEN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name   = '{table}'
                          AND column_name  = 'id'
                          AND column_default IS NOT NULL
                    ) THEN
                        ALTER TABLE {table}
                            ALTER COLUMN id SET DEFAULT gen_random_uuid();
                        RAISE NOTICE 'Added DEFAULT gen_random_uuid() to {table}.id';
                    END IF;
                END IF;
            END $$;
        """))

    # 3. Safety: re-run the filters insert from migration 006 in case it failed
    #    (idempotent — WHERE NOT EXISTS prevents duplicates)
    op.execute(sa.text(f"""
        INSERT INTO config_profiles (id, user_id, config_type, config_json, pool_id)
        SELECT
            gen_random_uuid(),
            user_id,
            'filters',
            '{_DEFAULT_FILTERS}'::jsonb,
            NULL
        FROM config_profiles
        WHERE config_type = 'signal'
          AND pool_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM config_profiles cp2
              WHERE cp2.user_id = config_profiles.user_id
                AND cp2.config_type = 'filters'
                AND cp2.pool_id IS NULL
          );
    """))


def downgrade() -> None:
    # Remove DEFAULT from id columns (reverting to Python-only uuid generation)
    for table in _UUID_PK_TABLES:
        op.execute(sa.text(f"""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = '{table}'
                ) THEN
                    ALTER TABLE {table} ALTER COLUMN id DROP DEFAULT;
                END IF;
            END $$;
        """))
