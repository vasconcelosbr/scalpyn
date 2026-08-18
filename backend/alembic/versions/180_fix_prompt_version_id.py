"""Correct the primary key of the systemic-multimodule@2.0.5 prompt version row.

Revision ID: 180_fix_prompt_version_id
Revises: 179_ceiling_1m_context

Every prompt version's id is deterministic: initial_prompts.py's _prompt()
helper computes it as uuid5(NAMESPACE_URL, f"scalpyn:prompt:{key}:{version}"),
and every seeding migration through 2.0.4 used that same scheme. Migration
160_systemic_output_budget (which introduced 2.0.5) instead computed the id
with uuid5(uuid.UUID("809e4f74-e34b-54d9-b611-4ee53a33198f"),
"systemic-multimodule@2.0.5") -- a different namespace and a different name
string. That inserted 39e3f0b7-485a-596a-bbe0-bd9dcb700668 into
ai_prompt_versions, while the application computes
743ef359-8cb9-5caf-a75e-bbcb6a5958fe for the same logical version at runtime.

module_ai_analysis_service.py's create_run() still pinned "2.0.4" precisely
because bumping it to "2.0.5" hits this mismatch: inserting an ai_requests
row with prompt_version_id=743ef359-... raises
ai_requests_prompt_version_id_fkey (asyncpg.ForeignKeyViolationError) since
no ai_prompt_versions row has that id. This migration corrects the row's id
in place so the pin can be bumped safely. No ai_requests row has ever
referenced the wrong id (verified before every run), so this is a pure
key correction with nothing to cascade.
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa


revision = "180_fix_prompt_version_id"
down_revision = "179_ceiling_1m_context"
branch_labels = None
depends_on = None

PROMPT_KEY = "systemic-multimodule"
SEMANTIC_VERSION = "2.0.5"
WRONG_ID = "39e3f0b7-485a-596a-bbe0-bd9dcb700668"
CORRECT_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, f"scalpyn:prompt:{PROMPT_KEY}:{SEMANTIC_VERSION}"))


def _repoint_id(*, from_id: str, to_id: str) -> None:
    bind = op.get_bind()
    row = bind.execute(sa.text("""
        SELECT id FROM ai_prompt_versions
         WHERE prompt_key=:prompt_key AND semantic_version=:semantic_version
    """), {"prompt_key": PROMPT_KEY, "semantic_version": SEMANTIC_VERSION}).mappings().one()
    if str(row["id"]) != from_id:
        raise RuntimeError("PROMPT_VERSION_ID_ALREADY_MIGRATED_OR_UNEXPECTED")
    referencing = bind.execute(sa.text(
        "SELECT count(*) AS n FROM ai_requests WHERE prompt_version_id = CAST(:id AS uuid)"
    ), {"id": from_id}).mappings().one()
    if int(referencing["n"]) > 0:
        raise RuntimeError("PROMPT_VERSION_ID_HAS_LIVE_REFERENCES")
    bind.execute(sa.text("""
        UPDATE ai_prompt_versions SET id=CAST(:to_id AS uuid)
         WHERE prompt_key=:prompt_key AND semantic_version=:semantic_version
    """), {"to_id": to_id, "prompt_key": PROMPT_KEY, "semantic_version": SEMANTIC_VERSION})


def upgrade() -> None:
    _repoint_id(from_id=WRONG_ID, to_id=CORRECT_ID)


def downgrade() -> None:
    _repoint_id(from_id=CORRECT_ID, to_id=WRONG_ID)
