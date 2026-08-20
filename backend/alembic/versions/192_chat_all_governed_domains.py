"""Enable typed, human-approved governed changes across Scalpyn configuration domains.

Revision ID: 192_chat_all_governed_domains
Revises: 191_chat_explicit_proposal
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "192_chat_all_governed_domains"
down_revision = "191_chat_explicit_proposal"
branch_labels = None
depends_on = None

PROMPT_NAMESPACE = uuid.UUID("fd23965c-3e46-5c8a-a62a-b4a67624770d")
GRAPH_NAMESPACE = uuid.UUID("a42c5ab1-1bda-5e45-ae66-554315834a7d")
MODULE_NAMESPACE = uuid.UUID("2a866345-2215-57f8-8054-5803d4a896dc")
APPROVED_AT = datetime(2026, 8, 20, 22, 0, tzinfo=timezone.utc)
PROMPT_KEY = "analysis-chat-governed-change"
FROM_VERSION = "1.12.0"
TO_VERSION = "1.13.0"

OLD_LIMIT_TEXT = (
    "Every other config family, runtime gate, order/exchange action, secret, arbitrary SQL/code "
    "and ML promotion must return LIMITATION."
)
NEW_LIMIT_TEXT = (
    "The registered global configuration families are score (the Score Engine master), "
    "spot_engine, futures_engine, risk, strategy, ml and social_score. ML means the existing "
    "persisted ML configuration document only; training, retraining, model-registry status and "
    "promotion are separate lifecycle actions and remain outside this proposal contract. Social "
    "Score remains subject to its freshness gate. Runtime/provider gates, order or exchange "
    "actions, secrets and arbitrary SQL or code must return LIMITATION."
)

PROFILE_AUTHORITY_TEXT = (
    " For Strategy Profiles, UPDATE_PROFILE_CONFIG and UPDATE_PROFILE_CONFIG_SET support only "
    "the existing roots filters, scoring, signals, block_rules, entry_triggers and "
    "default_timeframe. Use the exact profile_id from strategy_profiles.get_profile. The backend "
    "re-reads each owned Profile, binds the current old value and every array identity guard, and "
    "validates the complete profile before preview. To append a rule or condition, use the "
    "standard path suffix /- with op=add; the backend materializes the authoritative array index. "
    "The refreshed evidence menu includes the current Strategy Profile, Score Engine, ML, Social "
    "Score, Global Risk and Strategies documents. A final human confirmation is always required "
    "after the exact diff is displayed, and the hard risk and strategy vetoes remain effective."
)

ALLOWED_CONFIG_TYPES = [
    "score",
    "spot_engine",
    "futures_engine",
    "risk",
    "strategy",
    "ml",
    "social_score",
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
    if OLD_LIMIT_TEXT not in system_template:
        raise RuntimeError("CHAT_ALL_DOMAINS_AUTHORITY_TEXT_NOT_FOUND")
    content["system_template"] = system_template.replace(
        OLD_LIMIT_TEXT,
        NEW_LIMIT_TEXT + PROFILE_AUTHORITY_TEXT,
        1,
    )
    schema = deepcopy(content["output_schema_json"])
    try:
        config_type = (
            schema["properties"]["proposal"]["anyOf"][0]["properties"]
            ["target"]["properties"]["config_type"]
        )
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("CHAT_ALL_DOMAINS_SCHEMA_SHAPE_INVALID") from exc
    if config_type.get("enum") != [
        "score", "spot_engine", "futures_engine", "risk", "strategy", None,
    ]:
        raise RuntimeError("CHAT_ALL_DOMAINS_SCHEMA_SCOPE_UNEXPECTED")
    config_type["enum"] = list(ALLOWED_CONFIG_TYPES)
    content["output_schema_json"] = schema
    content["semantic_version"] = TO_VERSION
    return content


def _insert_module_revision(bind, module_key: str, new_tool: str) -> None:
    previous = bind.execute(sa.text("""
        SELECT entities, read_tools, write_tools, dependencies,
               freshness_sla_seconds, risk_class, tenant_scoped
          FROM ai_module_capabilities
         WHERE module_key = :module_key
           AND semantic_version = '1.0.0'
           AND status = 'APPROVED'
    """), {"module_key": module_key}).mappings().one()
    read_tools = list(previous["read_tools"])
    if new_tool not in read_tools:
        read_tools.append(new_tool)
    payload = {
        "module_key": module_key,
        "version": "1.1.0",
        "entities": list(previous["entities"]),
        "read_tools": read_tools,
        "write_tools": list(previous["write_tools"]),
        "dependencies": list(previous["dependencies"]),
        "freshness_sla_seconds": previous["freshness_sla_seconds"],
        "risk_class": previous["risk_class"],
        "tenant_scoped": bool(previous["tenant_scoped"]),
        "status": "APPROVED",
    }
    bind.execute(sa.text("""
        INSERT INTO ai_module_capabilities (
            id, module_key, semantic_version, entities, read_tools, write_tools,
            dependencies, freshness_sla_seconds, risk_class, tenant_scoped,
            content_hash, status, created_at, approved_at
        ) VALUES (
            CAST(:id AS uuid), :module_key, '1.1.0', CAST(:entities AS jsonb),
            CAST(:read_tools AS jsonb), CAST(:write_tools AS jsonb),
            CAST(:dependencies AS jsonb), :freshness_sla_seconds, :risk_class,
            :tenant_scoped, :content_hash, 'APPROVED', :created_at, :approved_at
        ) ON CONFLICT (module_key, semantic_version) DO NOTHING
    """), {
        "id": str(uuid.uuid5(MODULE_NAMESPACE, f"{module_key}@1.1.0")),
        "module_key": module_key,
        "entities": json.dumps(payload["entities"]),
        "read_tools": json.dumps(payload["read_tools"]),
        "write_tools": json.dumps(payload["write_tools"]),
        "dependencies": json.dumps(payload["dependencies"]),
        "freshness_sla_seconds": payload["freshness_sla_seconds"],
        "risk_class": payload["risk_class"],
        "tenant_scoped": payload["tenant_scoped"],
        "content_hash": _canonical_hash(payload),
        "created_at": APPROVED_AT,
        "approved_at": APPROVED_AT,
    })


def upgrade() -> None:
    bind = op.get_bind()
    previous_prompt = bind.execute(sa.text("""
        SELECT system_template, user_template, input_schema_json,
               output_schema_json, tool_policy_json, provider_constraints_json
          FROM ai_prompt_versions
         WHERE prompt_key = :prompt_key
           AND semantic_version = :from_version
           AND status = 'APPROVED'
    """), {"prompt_key": PROMPT_KEY, "from_version": FROM_VERSION}).mappings().one()
    content = _expanded_prompt(dict(previous_prompt))
    content["prompt_key"] = PROMPT_KEY
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
        "id": str(uuid.uuid5(PROMPT_NAMESPACE, f"{PROMPT_KEY}@{TO_VERSION}")),
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
    })

    previous_graph = bind.execute(sa.text("""
        SELECT graph_key, state_schema_version, node_manifest, edge_manifest
          FROM ai_graph_definitions
         WHERE graph_key = 'analysis-chat-v1'
           AND semantic_version = '1.3.0'
           AND status = 'APPROVED'
    """)).mappings().one()
    graph = dict(previous_graph)
    graph["semantic_version"] = "1.4.0"
    graph["tool_policy_version"] = "analysis-chat-governed-write-policy-v4"
    bind.execute(sa.text("""
        INSERT INTO ai_graph_definitions (
            id, graph_key, semantic_version, state_schema_version, status,
            content_hash, code_revision, node_manifest, edge_manifest,
            tool_policy_version, created_at, approved_at
        ) VALUES (
            CAST(:id AS uuid), :graph_key, :semantic_version, :state_schema_version,
            'APPROVED', :content_hash, :code_revision, CAST(:node_manifest AS jsonb),
            CAST(:edge_manifest AS jsonb), :tool_policy_version, :created_at, :approved_at
        ) ON CONFLICT (graph_key, semantic_version) DO NOTHING
    """), {
        "id": str(uuid.uuid5(GRAPH_NAMESPACE, "analysis-chat-v1@1.4.0")),
        **graph,
        "content_hash": _canonical_hash(graph),
        "code_revision": revision,
        "node_manifest": json.dumps(graph["node_manifest"]),
        "edge_manifest": json.dumps(graph["edge_manifest"]),
        "created_at": APPROVED_AT,
        "approved_at": APPROVED_AT,
    })

    _insert_module_revision(bind, "ml_models", "ml_models.get_governed_configuration")
    _insert_module_revision(bind, "social_score", "social_score.get_governed_configuration")


def downgrade() -> None:
    op.execute(sa.text("""
        DELETE FROM ai_module_capabilities
         WHERE semantic_version = '1.1.0'
           AND module_key IN ('ml_models', 'social_score')
    """))
    op.execute(sa.text("""
        DELETE FROM ai_graph_definitions
         WHERE graph_key = 'analysis-chat-v1' AND semantic_version = '1.4.0'
    """))
    op.execute(sa.text("""
        DELETE FROM ai_prompt_versions
         WHERE prompt_key = 'analysis-chat-governed-change' AND semantic_version = '1.13.0'
    """))
