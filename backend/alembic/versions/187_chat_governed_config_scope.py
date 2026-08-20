"""Expand Analysis Chat's human-approved governed configuration scope.

Revision ID: 187_chat_governed_config_scope
Revises: 186_deepseek_output_16k

The execution service already owns the preview, typed patch, optimistic
concurrency, second human gate, audit and rollback workflow.  The active chat
prompt, however, still restricted UPDATE_CONFIG_PROFILE to score rule points
and explicitly ordered the model to return LIMITATION for spot, futures, risk
and strategy.  This prompt version exposes only the configuration families
that now have complete deterministic candidate and policy-semantic validators.
Trading/order authority, secrets, runtime gates, ML promotion and arbitrary
code/SQL remain outside the contract.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "187_chat_governed_config_scope"
down_revision = "186_deepseek_output_16k"
branch_labels = None
depends_on = None

PROMPT_NAMESPACE = uuid.UUID("fd23965c-3e46-5c8a-a62a-b4a67624770d")
APPROVED_AT = datetime(2026, 8, 20, tzinfo=timezone.utc)
PROMPT_KEY = "analysis-chat-governed-change"
FROM_VERSION = "1.9.1"
TO_VERSION = "1.11.0"

OLD_AUTHORITY_TEXT = (
    "UPDATE_CONFIG_PROFILE currently supports only the complete global score document with "
    "target.pool_id=null, and only through rule_id for scoring_rules edits. Spot, futures, "
    "risk, strategy and every other config family lack a complete governed semantic validator "
    "and must return LIMITATION."
)

NEW_AUTHORITY_TEXT = (
    "UPDATE_CONFIG_PROFILE supports only complete global configuration documents with "
    "target.pool_id=null for these registered config_type values: score, spot_engine, "
    "futures_engine, risk and strategy. Score remains restricted to rule_id-based "
    "scoring_rules point edits as described above. For spot_engine, futures_engine, risk and "
    "strategy, use an exact existing RFC 6901 path and current leaf value from evidence; the "
    "backend rebuilds and validates the complete typed document before offering the final human "
    "approval. In spot_engine, the governed trailing and kill-switch fields live at "
    "/sell_flow/trailing/activation_profit_pct, /sell_flow/trailing/hwm_trail_pct, "
    "/sell_flow/kill_switch/atr_stop_multiplier and "
    "/sell_flow/kill_switch/max_drawdown_from_hwm_pct. Every other config family, runtime gate, "
    "order/exchange action, secret, arbitrary SQL/code and ML promotion must return LIMITATION."
)

ALLOWED_CONFIG_TYPES = [
    "score",
    "spot_engine",
    "futures_engine",
    "risk",
    "strategy",
    None,
]


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _expanded_prompt(previous: dict[str, object]) -> dict[str, object]:
    content = deepcopy(previous)
    system_template = str(content["system_template"])
    if OLD_AUTHORITY_TEXT not in system_template:
        raise RuntimeError("CHAT_GOVERNED_CONFIG_AUTHORITY_TEXT_NOT_FOUND")
    content["system_template"] = system_template.replace(
        OLD_AUTHORITY_TEXT,
        NEW_AUTHORITY_TEXT,
        1,
    )

    schema = deepcopy(content["output_schema_json"])
    try:
        config_type = (
            schema["properties"]["proposal"]["anyOf"][0]["properties"]
            ["target"]["properties"]["config_type"]
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("CHAT_GOVERNED_CONFIG_SCHEMA_SHAPE_INVALID") from exc
    if config_type.get("enum") != ["score", None]:
        raise RuntimeError("CHAT_GOVERNED_CONFIG_SCHEMA_SCOPE_UNEXPECTED")
    config_type["enum"] = list(ALLOWED_CONFIG_TYPES)
    content["output_schema_json"] = schema
    content["semantic_version"] = TO_VERSION
    return content


def upgrade() -> None:
    bind = op.get_bind()
    previous = bind.execute(sa.text("""
        SELECT prompt_key, semantic_version, system_template, user_template,
               input_schema_json, output_schema_json, tool_policy_json,
               provider_constraints_json
          FROM ai_prompt_versions
         WHERE prompt_key = :prompt_key
           AND semantic_version = :from_version
           AND status = 'APPROVED'
    """), {
        "prompt_key": PROMPT_KEY,
        "from_version": FROM_VERSION,
    }).mappings().one()
    content = _expanded_prompt(dict(previous))
    content["prompt_key"] = PROMPT_KEY

    prompt_id = str(uuid.uuid5(PROMPT_NAMESPACE, f"{PROMPT_KEY}@{TO_VERSION}"))
    parameters = {
        "id": prompt_id,
        "prompt_key": PROMPT_KEY,
        "semantic_version": TO_VERSION,
        "system_template": content["system_template"],
        "user_template": content["user_template"],
        "input_schema_json": json.dumps(content["input_schema_json"]),
        "output_schema_json": json.dumps(content["output_schema_json"]),
        "tool_policy_json": json.dumps(content["tool_policy_json"]),
        "provider_constraints_json": json.dumps(content["provider_constraints_json"]),
        "content_hash": _canonical_hash(content),
        "created_at": APPROVED_AT,
        "approved_at": APPROVED_AT,
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
        ) ON CONFLICT (prompt_key, semantic_version) DO UPDATE SET
            system_template = EXCLUDED.system_template,
            user_template = EXCLUDED.user_template,
            input_schema_json = EXCLUDED.input_schema_json,
            output_schema_json = EXCLUDED.output_schema_json,
            tool_policy_json = EXCLUDED.tool_policy_json,
            provider_constraints_json = EXCLUDED.provider_constraints_json,
            status = 'APPROVED',
            content_hash = EXCLUDED.content_hash,
            approved_at = EXCLUDED.approved_at
    """), parameters)


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM ai_prompt_versions
         WHERE prompt_key = 'analysis-chat-governed-change'
           AND semantic_version = '1.11.0'
    """))
