"""Create ai_skills table

Revision ID: 010_ai_skills
Revises: 009_profile_role_autopilot
Create Date: 2026-03-25
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "010_ai_skills"
down_revision = "009_profile_role_autopilot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        DO $$
        BEGIN
            CREATE TABLE IF NOT EXISTS ai_skills (
                id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name        VARCHAR(120) NOT NULL,
                description TEXT,
                role_key    VARCHAR(60),
                prompt_text TEXT        NOT NULL,
                is_active   BOOLEAN     NOT NULL DEFAULT TRUE,
                created_at  TIMESTAMPTZ DEFAULT now(),
                updated_at  TIMESTAMPTZ DEFAULT now()
            );

            -- Unique constraint on (user_id, name)
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_ai_skill_user_name'
            ) THEN
                ALTER TABLE ai_skills
                    ADD CONSTRAINT uq_ai_skill_user_name UNIQUE (user_id, name);
            END IF;

            -- Index on user_id for fast per-user queries
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'ai_skills' AND indexname = 'ix_ai_skills_user_id'
            ) THEN
                CREATE INDEX ix_ai_skills_user_id ON ai_skills (user_id);
            END IF;

            -- Index on role_key for service lookups
            IF NOT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'ai_skills' AND indexname = 'ix_ai_skills_role_key'
            ) THEN
                CREATE INDEX ix_ai_skills_role_key ON ai_skills (role_key);
            END IF;
        END
        $$;
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS ai_skills CASCADE;"))
