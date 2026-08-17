"""Force analysis-chat responses into the caller's requested language.

Revision ID: 174_chat_response_language
Revises: 173_ai_profile_model_variants

Both live analysis-chat prompts ("analysis-chat-system" and
"analysis-chat-governed-change") told the model to "Answer in the question
language" -- a heuristic, not an instruction. The API request already
carries a response_language field (default "pt-BR", see
CreateMessageRequest) that gets stored on the request but was never
actually read back into the prompt sent to the model, so it had no effect.
When a question mixes English technical terms into a Portuguese sentence
("valide os profiles em LEGACY") the heuristic can misfire and the model
answers in English. This wires the existing field into both templates via a
new {response_language} placeholder and replaces the heuristic with an
explicit instruction.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "174_chat_response_language"
down_revision = "173_ai_profile_model_variants"
branch_labels = None
depends_on = None

PROMPT_NAMESPACE = uuid.UUID("809e4f74-e34b-54d9-b611-4ee53a33198f")
APPROVED_AT = datetime(2026, 8, 17, tzinfo=timezone.utc)

LANGUAGE_INSTRUCTION = (
    "The required response language is {response_language} (a BCP-47 code; "
    "pt-BR means Brazilian Portuguese). Always answer in that language, "
    "regardless of the language mixed into the question, evidence, or field "
    "names."
)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bump(*, prompt_key: str, from_version: str, to_version: str) -> None:
    bind = op.get_bind()
    previous = bind.execute(sa.text("""
        SELECT system_template, user_template, input_schema_json,
               output_schema_json, tool_policy_json, provider_constraints_json
          FROM ai_prompt_versions
         WHERE prompt_key = :prompt_key
           AND semantic_version = :from_version
           AND status = 'APPROVED'
    """), {"prompt_key": prompt_key, "from_version": from_version}).mappings().one()

    new_system_template = previous["system_template"].replace(
        "Answer in the question language.", LANGUAGE_INSTRUCTION,
    )
    if new_system_template == previous["system_template"]:
        raise RuntimeError(f"CHAT_PROMPT_LANGUAGE_HEURISTIC_NOT_FOUND:{prompt_key}")

    content = {
        "prompt_key": prompt_key,
        "semantic_version": to_version,
        "system_template": new_system_template,
        "user_template": previous["user_template"],
        "input_schema_json": previous["input_schema_json"],
        "output_schema_json": previous["output_schema_json"],
        "tool_policy_json": previous["tool_policy_json"],
        "provider_constraints_json": previous["provider_constraints_json"],
    }
    bind.execute(sa.text("""
        INSERT INTO ai_prompt_versions (
            id, prompt_key, semantic_version, system_template, user_template,
            input_schema_json, output_schema_json, tool_policy_json,
            provider_constraints_json, status, content_hash, created_at, approved_at
        ) VALUES (
            CAST(:id AS uuid), :prompt_key, :semantic_version, :system_template, :user_template,
            CAST(:input_schema_json AS jsonb), CAST(:output_schema_json AS jsonb),
            CAST(:tool_policy_json AS jsonb), CAST(:provider_constraints_json AS jsonb),
            'APPROVED', :content_hash, :created_at, :approved_at
        ) ON CONFLICT (prompt_key, semantic_version) DO NOTHING
    """), {
        "id": str(uuid.uuid5(PROMPT_NAMESPACE, f"{prompt_key}@{to_version}")),
        "prompt_key": content["prompt_key"],
        "semantic_version": content["semantic_version"],
        "system_template": content["system_template"],
        "user_template": content["user_template"],
        # bind.execute() over an async JSONB column returns already-decoded
        # Python dicts, not JSON text -- a CAST(:x AS jsonb) bind parameter
        # needs a string. Passing the dict directly raises
        # asyncpg.exceptions.DataError: 'dict' object has no attribute
        # 'encode' (caught live on 2026-08-17: it silently fell through to
        # the boot script's `alembic stamp head` fallback, which stamped the
        # revision as applied without ever inserting these rows).
        "input_schema_json": json.dumps(content["input_schema_json"]),
        "output_schema_json": json.dumps(content["output_schema_json"]),
        "tool_policy_json": json.dumps(content["tool_policy_json"]),
        "provider_constraints_json": json.dumps(content["provider_constraints_json"]),
        "content_hash": _canonical_hash(content),
        "created_at": APPROVED_AT,
        "approved_at": APPROVED_AT,
    })


def upgrade() -> None:
    # from_version is the row actually selected by analysis_chat_service.py's
    # `ORDER BY approved_at DESC, semantic_version DESC LIMIT 1` -- NOT
    # necessarily the highest semantic_version. analysis-chat-governed-change
    # 1.10.0 exists in this table but was never promoted (stale approved_at),
    # so 1.9.0 is what production actually serves today; 1.10.0 also contains
    # unescaped literal braces (e.g. {"indicator":"rsi"}) in system_template
    # that would raise KeyError out of str.format_map the moment it became
    # live, so it must not be used as the base for this bump.
    _bump(prompt_key="analysis-chat-system", from_version="1.1.0", to_version="1.2.0")
    _bump(prompt_key="analysis-chat-governed-change", from_version="1.9.0", to_version="1.9.1")


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM ai_prompt_versions
         WHERE (prompt_key = 'analysis-chat-system' AND semantic_version = '1.2.0')
            OR (prompt_key = 'analysis-chat-governed-change' AND semantic_version = '1.9.1')
    """))
