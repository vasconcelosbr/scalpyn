"""Require concrete paths in governed Analysis Chat proposals.

Revision ID: 167_chat_concrete_paths
Revises: 166_chat_compact_proposals
Create Date: 2026-08-13
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "167_chat_concrete_paths"
down_revision = "166_chat_compact_proposals"
branch_labels = None
depends_on = None

PROMPT_NAMESPACE = uuid.UUID("5d43ac9b-20dd-58af-87a9-6dd732a4a23e")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _prompt_content() -> dict[str, object]:
    target = {
        "type": "object",
        "additionalProperties": False,
        "required": ["profile_id", "profile_name", "config_type", "pool_id", "profile_ids"],
        "properties": {
            "profile_id": {"type": ["string", "null"]},
            "profile_name": {"type": ["string", "null"]},
            "config_type": {
                "enum": ["score", None],
            },
            "pool_id": {"type": ["string", "null"]},
            "profile_ids": {
                "type": "array", "maxItems": 32,
                "items": {"type": "string", "format": "uuid"},
            },
        },
    }
    change = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "op", "path", "value_json", "old_value_json", "array_guards_json",
            "reason", "evidence_refs", "profile_id", "profile_name",
            "profile_indexes",
        ],
        "properties": {
            "op": {"enum": ["add", "replace", "remove"]},
            "path": {
                "type": "string", "minLength": 2, "maxLength": 500,
                "description": (
                    "Concrete RFC 6901 pointer into the persisted target document; "
                    "every array segment is a zero-based decimal index."
                ),
            },
            "value_json": {
                "type": "string", "maxLength": 4000,
                "description": "Compact JSON-encoded value; encode JSON null for remove.",
            },
            "old_value_json": {
                "type": "string", "maxLength": 4000,
                "description": (
                    "Compact JSON-encoded current leaf value copied from evidence; "
                    "encode JSON null only for add."
                ),
            },
            "array_guards_json": {
                "type": "string", "maxLength": 4000,
                "description": (
                    "Compact JSON array with one {path,identity} guard for every indexed "
                    "array element traversed by path; encode [] when no array is traversed."
                ),
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 240},
            "evidence_refs": {
                "type": "array", "minItems": 1, "maxItems": 4,
                "items": {"type": "string", "format": "uuid"},
            },
            "profile_id": {"type": ["string", "null"]},
            "profile_name": {"type": ["string", "null"]},
            "profile_indexes": {
                "type": "array", "maxItems": 32,
                "items": {"type": "integer", "minimum": 0, "maximum": 31},
            },
        },
    }
    proposal = {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation_type", "target", "objective", "risk", "changes"],
        "properties": {
            "operation_type": {"enum": [
                "UPDATE_PROFILE_CONFIG", "UPDATE_PROFILE_CONFIG_SET",
                "UPDATE_CONFIG_PROFILE", "SET_PROFILE_ACTIVE_STATUS",
            ]},
            "target": target,
            "objective": {"type": "string", "minLength": 1, "maxLength": 240},
            "risk": {"type": "string", "minLength": 1, "maxLength": 240},
            "changes": {"type": "array", "minItems": 1, "maxItems": 64, "items": change},
        },
    }
    output = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "answer", "answer_type", "based_on", "parent_analysis_run_id",
            "evidence_refs", "proposal",
        ],
        "properties": {
            "answer": {"type": "string", "maxLength": 240},
            "answer_type": {"enum": ["PROPOSAL", "LIMITATION"]},
            "based_on": {"enum": ["PROPOSAL_DRAFT"]},
            "parent_analysis_run_id": {"type": "string", "format": "uuid"},
            "evidence_refs": {
                "type": "array", "minItems": 1, "maxItems": 12,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["evidence_id"],
                    "properties": {"evidence_id": {"type": "string", "format": "uuid"}},
                },
            },
            "proposal": {"anyOf": [proposal, {"type": "null"}]},
        },
    }
    return {
        "prompt_key": "analysis-chat-governed-change",
        "semantic_version": "1.5.0",
        "system_template": (
            "You are the governed Scalpyn configuration action planner. Answer in the question "
            "language. Return one typed operation that the backend may apply only after a second "
            "explicit human confirmation. Every change.path is an exact RFC 6901 pointer into the "
            "persisted target document supplied in evidence. Arrays such as filters.conditions, "
            "signals.conditions, entry_triggers.conditions, block_rules.blocks and scoring.rules "
            "MUST use the actual zero-based decimal index from evidence; never use indicator names, "
            "rule names, IDs or invented aliases as array keys. Every indexed array element MUST also "
            "have one array_guards_json entry whose path ends at that index and whose identity copies "
            "stable persisted identity such as id, rule_id, field, indicator, name, or left+right. "
            "Every replace/remove MUST copy the exact current leaf into old_value_json; every add MUST "
            "use old_value_json=null and append to the current array length. UPDATE_PROFILE_CONFIG and "
            "UPDATE_PROFILE_CONFIG_SET "
            "may use only profile roots default_timeframe, filters, scoring, signals, block_rules "
            "and entry_triggers. A grouped profile change is valid only when the exact concrete path "
            "exists with the same meaning in every selected profile; otherwise emit profile-specific "
            "changes with each actual index. Global score rules do not live in profile scoring.rules: "
            "use UPDATE_CONFIG_PROFILE with target.config_type=score, derive the rule's current array "
            "index from supplied evidence, and guard it with the persisted rule id. Never copy a fixed "
            "example index. UPDATE_CONFIG_PROFILE currently supports only the complete global score "
            "document with target.pool_id=null. Spot, futures, risk, strategy and every other config "
            "family lack a complete governed semantic validator and must return LIMITATION. Exactly one proposal "
            "cannot mix global config and profile "
            "resources. Paths such as /scoring/rules/rsi_overbought_penalty, "
            "/filters/conditions/taker_ratio/value, /entry_triggers/conditions/adx_minimum and any "
            "unsupported /execution_policy profile root are invalid. If the request spans resource "
            "families, lacks an existing concrete path, or cannot fit one unambiguous operation, "
            "return answer_type=LIMITATION and proposal=null; never guess, coerce, skip profiles, or "
            "invent schema. For identical concrete changes across profiles, group with profile_indexes; "
            "group only when path, old value, array identities and new value are all identical; "
            "otherwise set profile_id and profile_name and leave profile_indexes empty. Every change "
            "must cite supplied evidence IDs. Orders, secrets, arbitrary SQL/code, ML promotion, "
            "deletion and runtime-gate modification remain outside this contract."
        ),
        "user_template": (
            "Parent analysis: {parent_analysis}\nEvidence: {evidence}\n"
            "Conversation: {conversation}\nConfirmed requested change: {question}"
        ),
        "input_schema_json": {"type": "object"},
        "output_schema_json": output,
        "tool_policy_json": {
            "default_mode": "DRAFT_PROPOSAL",
            "allow_side_effects": ["NONE", "AUDIT_WRITE", "PROPOSAL_WRITE"],
            "execution_requires_human_interrupt": True,
        },
        "provider_constraints_json": {"structured_output": True, "authority": "PROPOSAL_ONLY"},
    }


def upgrade() -> None:
    op.add_column(
        "ai_graph_runs",
        sa.Column("dispatch_kind", sa.String(length=16), nullable=False, server_default="START"),
    )
    op.add_column(
        "ai_graph_runs",
        sa.Column("dispatch_interrupt_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "ai_graph_runs",
        sa.Column("dispatch_decision_id", UUID(as_uuid=True), nullable=True),
    )
    # Older rows did not distinguish a fresh start from a human-decision
    # continuation.  Never infer and replay a legacy decision: its LangGraph
    # checkpoint may already be waiting at the next human gate.  Abort the
    # transactional migration so the operator can reconcile the active run
    # before any mixed-version worker is allowed to start.
    op.execute(sa.text("""
        DO $migration$
        BEGIN
            IF EXISTS (
                SELECT 1
                  FROM ai_graph_runs AS run
                 WHERE run.status IN ('QUEUED', 'RUNNING')
                   AND EXISTS (
                       SELECT 1
                         FROM ai_graph_interrupts AS interrupt
                        WHERE interrupt.graph_run_id = run.id
                          AND interrupt.status IN ('RESOLVED', 'REJECTED')
                   )
            ) THEN
                RAISE EXCEPTION
                    'ANALYSIS_CHAT_LEGACY_RESUME_RECONCILIATION_REQUIRED';
            END IF;
        END
        $migration$;
    """))
    op.create_check_constraint(
        "ck_ai_graph_run_dispatch_payload",
        "ai_graph_runs",
        "(dispatch_kind = 'START' AND dispatch_interrupt_id IS NULL "
        "AND dispatch_decision_id IS NULL) OR "
        "(dispatch_kind = 'RESUME' AND dispatch_interrupt_id IS NOT NULL "
        "AND dispatch_decision_id IS NOT NULL)",
    )

    bind = op.get_bind()
    approved_at = datetime(2026, 8, 13, tzinfo=timezone.utc)
    prompt = _prompt_content()
    prompt_id = str(uuid.uuid5(
        PROMPT_NAMESPACE,
        "analysis-chat-governed-change@1.5.0",
    ))
    content_hash = _canonical_hash(prompt)
    prompt_parameters = {
        "id": prompt_id,
        **prompt,
        "input_schema_json": json.dumps(prompt["input_schema_json"]),
        "output_schema_json": json.dumps(prompt["output_schema_json"]),
        "tool_policy_json": json.dumps(prompt["tool_policy_json"]),
        "provider_constraints_json": json.dumps(prompt["provider_constraints_json"]),
        "content_hash": content_hash,
        "created_at": approved_at,
        "approved_at": approved_at,
    }
    # Bind values on the statement itself so Alembic --sql with literal_binds
    # emits a complete, deployable artifact rather than NULL placeholders.
    prompt_upsert = sa.text("""
        INSERT INTO ai_prompt_versions (
            id,prompt_key,semantic_version,system_template,user_template,
            input_schema_json,output_schema_json,tool_policy_json,
            provider_constraints_json,status,content_hash,created_at,approved_at
        ) VALUES (
            CAST(:id AS uuid),:prompt_key,:semantic_version,:system_template,:user_template,
            CAST(:input_schema_json AS jsonb),CAST(:output_schema_json AS jsonb),
            CAST(:tool_policy_json AS jsonb),CAST(:provider_constraints_json AS jsonb),
            'APPROVED',:content_hash,:created_at,:approved_at
        ) ON CONFLICT (prompt_key,semantic_version) DO UPDATE SET
            system_template = EXCLUDED.system_template,
            user_template = EXCLUDED.user_template,
            input_schema_json = EXCLUDED.input_schema_json,
            output_schema_json = EXCLUDED.output_schema_json,
            tool_policy_json = EXCLUDED.tool_policy_json,
            provider_constraints_json = EXCLUDED.provider_constraints_json,
            status = 'APPROVED',
            approved_at = EXCLUDED.approved_at,
            deprecated_at = NULL
        WHERE ai_prompt_versions.id = EXCLUDED.id
          AND ai_prompt_versions.content_hash = EXCLUDED.content_hash
    """).bindparams(**prompt_parameters)
    op.execute(prompt_upsert)
    if op.get_context().as_sql:
        return
    persisted = bind.execute(sa.text("""
        SELECT id::text AS id, content_hash, status
          FROM ai_prompt_versions
         WHERE prompt_key = :prompt_key
           AND semantic_version = :semantic_version
    """).bindparams(
        prompt_key=prompt["prompt_key"],
        semantic_version=prompt["semantic_version"],
    )).mappings().one_or_none()
    if (
        persisted is None
        or persisted["id"] != prompt_id
        or persisted["content_hash"] != content_hash
        or persisted["status"] != "APPROVED"
    ):
        raise RuntimeError("ANALYSIS_CHAT_PROMPT_1_5_CONFLICT")


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE ai_prompt_versions
           SET status = 'DEPRECATED',
               deprecated_at = CURRENT_TIMESTAMP
         WHERE prompt_key = 'analysis-chat-governed-change'
           AND semantic_version = '1.5.0'
    """))
    op.drop_constraint(
        "ck_ai_graph_run_dispatch_payload", "ai_graph_runs", type_="check"
    )
    op.drop_column("ai_graph_runs", "dispatch_decision_id")
    op.drop_column("ai_graph_runs", "dispatch_interrupt_id")
    op.drop_column("ai_graph_runs", "dispatch_kind")
