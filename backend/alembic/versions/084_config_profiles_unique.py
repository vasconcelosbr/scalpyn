"""config_profiles unique constraint — prevent duplicate active global configs

Adds a partial unique index on (user_id, config_type) WHERE pool_id IS NULL AND is_active.
This prevents two active global configurations of the same type for the same user.

Before applying: audits for existing duplicates and deactivates older rows safely.
Aborts with a clear error if deactivation would be unsafe (unexpected count).

Revision ID: 084_config_profiles_unique
Revises: 083_profile_metrics_tables
Create Date: 2026-06-17
"""

from alembic import op
import sqlalchemy as sa

revision = "084_config_profiles_unique"
down_revision = "083_profile_metrics_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Audit duplicates
    dupes = conn.execute(sa.text("""
        SELECT user_id, config_type, COUNT(*) AS cnt
        FROM config_profiles
        WHERE pool_id IS NULL AND is_active = true
        GROUP BY user_id, config_type
        HAVING COUNT(*) > 1
    """)).fetchall()

    if dupes:
        # 2. For each duplicate group, keep the most recently updated row active,
        #    deactivate older ones. Safe: never deletes data, only sets is_active=false.
        for row in dupes:
            uid, ctype, cnt = row.user_id, row.config_type, row.cnt
            # Deactivate all but the latest updated_at
            conn.execute(sa.text("""
                UPDATE config_profiles
                SET is_active = false
                WHERE pool_id IS NULL
                  AND user_id   = :uid
                  AND config_type = :ctype
                  AND is_active = true
                  AND id NOT IN (
                      SELECT id FROM config_profiles
                      WHERE pool_id IS NULL
                        AND user_id   = :uid
                        AND config_type = :ctype
                        AND is_active = true
                      ORDER BY updated_at DESC NULLS LAST
                      LIMIT 1
                  )
            """), {"uid": str(uid), "ctype": ctype})

    # 3. Verify no duplicates remain before creating the index
    remaining = conn.execute(sa.text("""
        SELECT COUNT(*) AS cnt
        FROM (
            SELECT user_id, config_type
            FROM config_profiles
            WHERE pool_id IS NULL AND is_active = true
            GROUP BY user_id, config_type
            HAVING COUNT(*) > 1
        ) sub
    """)).scalar()

    if remaining and remaining > 0:
        raise RuntimeError(
            f"084_config_profiles_unique: {remaining} duplicate (user_id, config_type) groups "
            "remain after deactivation attempt. Manual review required before applying index."
        )

    op.execute(sa.text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_config_profiles_global_active
        ON config_profiles (user_id, config_type)
        WHERE pool_id IS NULL AND is_active = true
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS uq_config_profiles_global_active"))
