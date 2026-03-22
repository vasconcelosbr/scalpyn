"""Refactor: create filters config defaults, migrate signal → blocks entry_triggers

Revision ID: 006_refactor_filters_blocks
Revises: 005_catch_all_missing_columns
Create Date: 2026-03-22

What this migration does
------------------------
1. Creates a default 'filters' config_profile row for every user that currently
   has a 'signal' config (global, pool_id IS NULL).  Uses WHERE NOT EXISTS so
   it is idempotent.

2. Copies the signal conditions array into blocks.entry_triggers for every user
   that has both a 'block' and a 'signal' config row.  Uses jsonb_set, safe to
   re-run (overwrites entry_triggers with the same data on repeated runs).

3. Soft-deletes the old signal config rows by setting is_active = false (if the
   column exists).  Does NOT drop the rows — data is preserved.

All operations use raw SQL and are safe against any database state.
"""

from alembic import op
import sqlalchemy as sa

revision = "006_refactor_filters_blocks"
down_revision = "005_catch_all_missing_columns"
branch_labels = None
depends_on = None

_DEFAULT_FILTERS = """{
  "enabled": true,
  "logic": "AND",
  "filters": [
    {
      "id": "f_min_volume",
      "name": "Minimum 24h Volume",
      "enabled": true,
      "indicator": "volume_24h",
      "operator": ">=",
      "value": 1000000
    },
    {
      "id": "f_min_adx",
      "name": "Minimum Trend Strength (ADX)",
      "enabled": true,
      "indicator": "adx",
      "operator": ">=",
      "value": 20
    },
    {
      "id": "f_max_spread",
      "name": "Maximum Spread %",
      "enabled": true,
      "indicator": "spread_pct",
      "operator": "<=",
      "value": 0.5
    }
  ]
}"""


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Insert default filters config for each user that has a signal config
    # ------------------------------------------------------------------
    op.execute(sa.text(f"""
        INSERT INTO config_profiles (user_id, config_type, config_json, pool_id)
        SELECT
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

    # ------------------------------------------------------------------
    # 2. Copy signal conditions → blocks.entry_triggers
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        UPDATE config_profiles AS blk
        SET config_json = jsonb_set(
            blk.config_json,
            '{entry_triggers}',
            COALESCE(
                (
                    SELECT sig.config_json -> 'conditions'
                    FROM config_profiles sig
                    WHERE sig.user_id  = blk.user_id
                      AND sig.config_type = 'signal'
                      AND sig.pool_id IS NULL
                    LIMIT 1
                ),
                '[]'::jsonb
            )
        )
        WHERE blk.config_type = 'block'
          AND blk.pool_id IS NULL
          AND EXISTS (
              SELECT 1 FROM config_profiles sig2
              WHERE sig2.user_id     = blk.user_id
                AND sig2.config_type = 'signal'
                AND sig2.pool_id IS NULL
          );
    """))

    # ------------------------------------------------------------------
    # 3. Soft-delete signal configs (is_active column — safe with DO block)
    # ------------------------------------------------------------------
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name  = 'config_profiles'
                  AND column_name = 'is_active'
            ) THEN
                UPDATE config_profiles
                SET is_active = false
                WHERE config_type = 'signal';
            END IF;
        END
        $$;
    """))


def downgrade() -> None:
    # Reactivate signal configs (is_active)
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name  = 'config_profiles'
                  AND column_name = 'is_active'
            ) THEN
                UPDATE config_profiles
                SET is_active = true
                WHERE config_type = 'signal';
            END IF;
        END
        $$;
    """))

    # Remove injected entry_triggers from block configs
    op.execute(sa.text("""
        UPDATE config_profiles
        SET config_json = config_json - 'entry_triggers'
        WHERE config_type = 'block'
          AND pool_id IS NULL;
    """))

    # Remove filters configs created by this migration
    op.execute(sa.text("""
        DELETE FROM config_profiles WHERE config_type = 'filters';
    """))
