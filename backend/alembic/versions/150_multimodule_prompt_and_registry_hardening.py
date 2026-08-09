"""Seed the systemic v2 prompt and harden approved module immutability.

Revision ID: 150_multimodule_hardening
Revises: 149_multimodule_langgraph
"""

from __future__ import annotations

from datetime import datetime, timezone
import json

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "150_multimodule_hardening"
down_revision = "149_multimodule_langgraph"
branch_labels = None
depends_on = None

EXPECTED_PROMPT_HASH = "34eb1f1bc64910ddec313b7f1e308c88fb68c33bbf36e770d931e8301193ffa1"


def _prompt_values() -> dict:
    # The registry version is immutable. The fixed hash prevents a future
    # in-place edit of version 2.0.0 from silently changing migration history.
    from app.ai_orchestration.initial_prompts import initial_prompt_registry

    prompt = initial_prompt_registry().resolve("systemic-multimodule", "2.0.0")
    if prompt.content_hash != EXPECTED_PROMPT_HASH:
        raise RuntimeError("SYSTEMIC_MULTIMODULE_PROMPT_HASH_DRIFT")
    seeded_at = datetime(2026, 8, 8, tzinfo=timezone.utc)
    return {
        "id": prompt.id,
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
        "created_at": seeded_at,
        "approved_at": seeded_at,
    }


def _prompt_table():
    return sa.table(
        "ai_prompt_versions",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("prompt_key", sa.String),
        sa.column("semantic_version", sa.String),
        sa.column("status", sa.String),
        sa.column("system_template", sa.Text),
        sa.column("user_template", sa.Text),
        sa.column("input_schema_json", JSONB),
        sa.column("output_schema_json", JSONB),
        sa.column("tool_policy_json", JSONB),
        sa.column("provider_constraints_json", JSONB),
        sa.column("content_hash", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("approved_at", sa.DateTime(timezone=True)),
    )


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _jsonb_literal(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    # ``sa.text`` treats ``:1``, ``:false`` and similar JSON fragments as bind
    # parameters. Escaping colons preserves literal JSON online; SQLAlchemy
    # removes the escape when compiling both online and offline SQL.
    encoded = encoded.replace(":", r"\:")
    return f"{_sql_quote(encoded)}::jsonb"


def _prompt_seed_statements(values: dict) -> tuple[str, str]:
    conflict = f"""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM ai_prompt_versions
             WHERE prompt_key = {_sql_quote(values['prompt_key'])}
               AND semantic_version = {_sql_quote(values['semantic_version'])}
               AND content_hash <> {_sql_quote(EXPECTED_PROMPT_HASH)}
          ) THEN
            RAISE EXCEPTION 'SYSTEMIC_MULTIMODULE_PROMPT_DATABASE_CONFLICT';
          END IF;
        END $$;
    """
    insert = f"""
        INSERT INTO ai_prompt_versions (
            id, prompt_key, semantic_version, status, system_template,
            user_template, input_schema_json, output_schema_json,
            tool_policy_json, provider_constraints_json, content_hash,
            created_at, approved_at
        )
        SELECT
            {_sql_quote(str(values['id']))}::uuid,
            {_sql_quote(values['prompt_key'])},
            {_sql_quote(values['semantic_version'])},
            {_sql_quote(values['status'])},
            {_sql_quote(values['system_template'])},
            {_sql_quote(values['user_template'])},
            {_jsonb_literal(values['input_schema_json'])},
            {_jsonb_literal(values['output_schema_json'])},
            {_jsonb_literal(values['tool_policy_json'])},
            {_jsonb_literal(values['provider_constraints_json'])},
            {_sql_quote(values['content_hash'])},
            {_sql_quote(values['created_at'].isoformat())}::timestamptz,
            {_sql_quote(values['approved_at'].isoformat())}::timestamptz
        WHERE NOT EXISTS (
            SELECT 1 FROM ai_prompt_versions
             WHERE prompt_key = {_sql_quote(values['prompt_key'])}
               AND semantic_version = {_sql_quote(values['semantic_version'])}
        );
    """
    return conflict, insert


def _harden_registry_trigger() -> None:
    op.execute(sa.text("""
        CREATE OR REPLACE FUNCTION prevent_approved_ai_module_capability_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'APPROVED' AND (
            NEW.module_key IS DISTINCT FROM OLD.module_key OR
            NEW.semantic_version IS DISTINCT FROM OLD.semantic_version OR
            NEW.entities IS DISTINCT FROM OLD.entities OR
            NEW.read_tools IS DISTINCT FROM OLD.read_tools OR
            NEW.write_tools IS DISTINCT FROM OLD.write_tools OR
            NEW.dependencies IS DISTINCT FROM OLD.dependencies OR
            NEW.freshness_sla_seconds IS DISTINCT FROM OLD.freshness_sla_seconds OR
            NEW.risk_class IS DISTINCT FROM OLD.risk_class OR
            NEW.tenant_scoped IS DISTINCT FROM OLD.tenant_scoped OR
            NEW.content_hash IS DISTINCT FROM OLD.content_hash OR
            NEW.status IS DISTINCT FROM OLD.status OR
            NEW.created_at IS DISTINCT FROM OLD.created_at OR
            NEW.approved_at IS DISTINCT FROM OLD.approved_at OR
            NEW.deprecated_at IS DISTINCT FROM OLD.deprecated_at
          ) THEN RAISE EXCEPTION 'approved AI module capability is immutable'; END IF;
          RETURN NEW;
        END $$;
    """))


def upgrade() -> None:
    values = _prompt_values()
    conflict_sql, insert_sql = _prompt_seed_statements(values)
    # asyncpg accepts a single SQL command per prepared statement. Keeping the
    # conflict guard and idempotent insert separate also preserves offline SQL.
    op.execute(sa.text(conflict_sql))
    op.execute(sa.text(insert_sql))
    _harden_registry_trigger()


def downgrade() -> None:
    op.execute(sa.text(f"""
        DELETE FROM ai_prompt_versions
         WHERE prompt_key = 'systemic-multimodule'
           AND semantic_version = '2.0.0'
           AND content_hash = '{EXPECTED_PROMPT_HASH}'
    """))
    # Revision 149 contains the same hardened function definition, so no
    # trigger change is needed when returning to that source revision.
