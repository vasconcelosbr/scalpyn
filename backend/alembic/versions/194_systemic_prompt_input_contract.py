"""Preserve saved analysis methodology under the canonical output contract.

Revision ID: 194_systemic_prompt_input_contract
Revises: 193_analysis_prompt_library
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa


revision = "194_systemic_prompt_input_contract"
down_revision = "193_analysis_prompt_library"
branch_labels = None
depends_on = None

EXPECTED_PROMPT_HASH = "ceb428fc76aa46548b90756af24016a17893bf8dd34724539d9a1d0a79b5d62d"


def _prompt_values() -> dict:
    from app.ai_orchestration.initial_prompts import initial_prompt_registry

    prompt = initial_prompt_registry().resolve("systemic-multimodule", "2.0.6")
    if prompt.content_hash != EXPECTED_PROMPT_HASH:
        raise RuntimeError("SYSTEMIC_MULTIMODULE_PROMPT_2_0_6_HASH_DRIFT")
    seeded_at = datetime(2026, 8, 21, 15, 30, tzinfo=timezone.utc)
    return {
        "id": prompt.id,
        "prompt_key": prompt.prompt_key,
        "semantic_version": prompt.semantic_version,
        "status": prompt.status,
        "system_template": prompt.system_template,
        "user_template": prompt.user_template,
        "input_schema_json": json.dumps(prompt.input_schema_json, separators=(",", ":"), ensure_ascii=False),
        "output_schema_json": json.dumps(prompt.output_schema_json, separators=(",", ":"), ensure_ascii=False),
        "tool_policy_json": json.dumps(prompt.tool_policy_json, separators=(",", ":"), ensure_ascii=False),
        "provider_constraints_json": json.dumps(
            prompt.provider_constraints_json, separators=(",", ":"), ensure_ascii=False,
        ),
        "content_hash": prompt.content_hash,
        "created_at": seeded_at,
        "approved_at": seeded_at,
    }


def upgrade() -> None:
    values = _prompt_values()
    bind = op.get_bind()
    conflict = bind.execute(
        sa.text("""
            SELECT content_hash
              FROM ai_prompt_versions
             WHERE prompt_key = 'systemic-multimodule'
               AND semantic_version = '2.0.6'
        """)
    ).scalar_one_or_none()
    if conflict is not None and conflict != EXPECTED_PROMPT_HASH:
        raise RuntimeError("SYSTEMIC_MULTIMODULE_PROMPT_2_0_6_DATABASE_CONFLICT")
    bind.execute(
        sa.text("""
            INSERT INTO ai_prompt_versions (
                id, prompt_key, semantic_version, status, system_template,
                user_template, input_schema_json, output_schema_json,
                tool_policy_json, provider_constraints_json, content_hash,
                created_at, approved_at
            ) VALUES (
                :id, :prompt_key, :semantic_version, :status, :system_template,
                :user_template, CAST(:input_schema_json AS jsonb), CAST(:output_schema_json AS jsonb),
                CAST(:tool_policy_json AS jsonb), CAST(:provider_constraints_json AS jsonb), :content_hash,
                :created_at, :approved_at
            )
            ON CONFLICT DO NOTHING
        """),
        values,
    )
    persisted = bind.execute(
        sa.text("""
            SELECT content_hash
              FROM ai_prompt_versions
             WHERE prompt_key = 'systemic-multimodule'
               AND semantic_version = '2.0.6'
        """)
    ).scalar_one_or_none()
    if persisted != EXPECTED_PROMPT_HASH:
        raise RuntimeError("SYSTEMIC_MULTIMODULE_PROMPT_2_0_6_PERSISTENCE_FAILED")


def downgrade() -> None:
    bind = op.get_bind()
    references = bind.execute(
        sa.text("""
            SELECT count(*)
              FROM ai_requests r
              JOIN ai_prompt_versions p ON p.id = r.prompt_version_id
             WHERE p.prompt_key = 'systemic-multimodule'
               AND p.semantic_version = '2.0.6'
        """)
    ).scalar_one()
    if references:
        raise RuntimeError("SYSTEMIC_MULTIMODULE_PROMPT_2_0_6_IN_USE")
    bind.execute(
        sa.text("""
            DELETE FROM ai_prompt_versions
             WHERE prompt_key = 'systemic-multimodule'
               AND semantic_version = '2.0.6'
               AND content_hash = :content_hash
        """),
        {"content_hash": EXPECTED_PROMPT_HASH},
    )
