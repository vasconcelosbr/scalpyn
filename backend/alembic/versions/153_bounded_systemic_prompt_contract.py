"""Seed the bounded systemic provider output contract.

Revision ID: 153_bounded_systemic_prompt
Revises: 152_concise_systemic_prompt
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision = "153_bounded_systemic_prompt"
down_revision = "152_concise_systemic_prompt"
branch_labels = None
depends_on = None

EXPECTED_PROMPT_HASH = "50ff6cd844b1974595e8ffd7dbc0d5f9386d48cb9111b4a3e3cd951dfe589026"


def _prompt_values() -> dict:
    from app.ai_orchestration.initial_prompts import initial_prompt_registry

    prompt = initial_prompt_registry().resolve("systemic-multimodule", "2.0.3")
    if prompt.content_hash != EXPECTED_PROMPT_HASH:
        raise RuntimeError("SYSTEMIC_MULTIMODULE_PROMPT_2_0_3_HASH_DRIFT")
    seeded_at = datetime(2026, 8, 10, tzinfo=timezone.utc)
    return {
        "id": str(prompt.id),
        "prompt_key": prompt.prompt_key,
        "semantic_version": prompt.semantic_version,
        "status": prompt.status,
        "system_template": prompt.system_template,
        "user_template": prompt.user_template,
        "input_schema_json": prompt.input_schema_json,
        "output_schema_json": prompt.output_schema_json,
        "tool_policy_json": prompt.tool_policy_json,
        "provider_constraints_json": prompt.provider_constraints_json,
        "content_hash": prompt.content_hash,
        "created_at": seeded_at.isoformat(),
        "approved_at": seeded_at.isoformat(),
    }


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''").replace(":", r"\:") + "'"


def _jsonb(value: object) -> str:
    return f"{_quote(json.dumps(value, separators=(',', ':'), ensure_ascii=False))}::jsonb"


def upgrade() -> None:
    value = _prompt_values()
    op.execute(sa.text(f"""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM ai_prompt_versions
             WHERE prompt_key = 'systemic-multimodule'
               AND semantic_version = '2.0.3'
               AND content_hash <> '{EXPECTED_PROMPT_HASH}'
          ) THEN
            RAISE EXCEPTION 'SYSTEMIC_MULTIMODULE_PROMPT_2_0_3_DATABASE_CONFLICT';
          END IF;
        END $$;
    """))
    op.execute(sa.text(f"""
        INSERT INTO ai_prompt_versions (
            id, prompt_key, semantic_version, status, system_template,
            user_template, input_schema_json, output_schema_json,
            tool_policy_json, provider_constraints_json, content_hash,
            created_at, approved_at
        )
        SELECT
            {_quote(value['id'])}::uuid,
            {_quote(value['prompt_key'])},
            {_quote(value['semantic_version'])},
            {_quote(value['status'])},
            {_quote(value['system_template'])},
            {_quote(value['user_template'])},
            {_jsonb(value['input_schema_json'])},
            {_jsonb(value['output_schema_json'])},
            {_jsonb(value['tool_policy_json'])},
            {_jsonb(value['provider_constraints_json'])},
            {_quote(value['content_hash'])},
            {_quote(value['created_at'])}::timestamptz,
            {_quote(value['approved_at'])}::timestamptz
        WHERE NOT EXISTS (
            SELECT 1 FROM ai_prompt_versions
             WHERE prompt_key = 'systemic-multimodule'
               AND semantic_version = '2.0.3'
        );
    """))


def downgrade() -> None:
    op.execute(sa.text(f"""
        DELETE FROM ai_prompt_versions
         WHERE prompt_key = 'systemic-multimodule'
           AND semantic_version = '2.0.3'
           AND content_hash = '{EXPECTED_PROMPT_HASH}'
    """))
