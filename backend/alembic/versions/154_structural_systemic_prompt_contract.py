"""Seed the structural systemic provider output contract.

Revision ID: 154_structural_systemic_prompt
Revises: 153_bounded_systemic_prompt
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision = "154_structural_systemic_prompt"
down_revision = "153_bounded_systemic_prompt"
branch_labels = None
depends_on = None

EXPECTED_PROMPT_HASH = "87cbd77b9eb587fafc540363c2d86232f3c33d3b12111a114f63ef99171eeb8e"


def _prompt_values() -> dict:
    from app.ai_orchestration.initial_prompts import initial_prompt_registry

    prompt = initial_prompt_registry().resolve("systemic-multimodule", "2.0.4")
    if prompt.content_hash != EXPECTED_PROMPT_HASH:
        raise RuntimeError("SYSTEMIC_MULTIMODULE_PROMPT_2_0_4_HASH_DRIFT")
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
               AND semantic_version = '2.0.4'
               AND content_hash <> '{EXPECTED_PROMPT_HASH}'
          ) THEN
            RAISE EXCEPTION 'SYSTEMIC_MULTIMODULE_PROMPT_2_0_4_DATABASE_CONFLICT';
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
               AND semantic_version = '2.0.4'
        );
    """))


def downgrade() -> None:
    op.execute(sa.text(f"""
        DELETE FROM ai_prompt_versions
         WHERE prompt_key = 'systemic-multimodule'
           AND semantic_version = '2.0.4'
           AND content_hash = '{EXPECTED_PROMPT_HASH}'
    """))
