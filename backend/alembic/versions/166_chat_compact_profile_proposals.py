"""Compact governed multi-profile proposals.

Revision ID: 166_chat_compact_proposals
Revises: 165_chat_bulk_profile_config
Create Date: 2026-08-13
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "166_chat_compact_proposals"
down_revision = "165_chat_bulk_profile_config"
branch_labels = None
depends_on = None

PROMPT_NAMESPACE = uuid.UUID("2dd52844-e393-56c3-956c-842d2fae8864")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _prompt_content() -> dict[str, object]:
    target = {
        "type": "object",
        "required": ["profile_id", "profile_name", "config_type", "pool_id", "profile_ids"],
        "properties": {
            "profile_id": {"type": ["string", "null"]},
            "profile_name": {"type": ["string", "null"]},
            "config_type": {"type": ["string", "null"]},
            "pool_id": {"type": ["string", "null"]},
            "profile_ids": {
                "type": "array", "maxItems": 32,
                "items": {"type": "string", "format": "uuid"},
            },
        },
    }
    change = {
        "type": "object",
        "required": [
            "op", "path", "value_json", "reason", "evidence_refs",
            "profile_id", "profile_name", "profile_indexes",
        ],
        "properties": {
            "op": {"enum": ["add", "replace", "remove"]},
            "path": {"type": "string", "minLength": 2, "maxLength": 500},
            "value_json": {
                "type": "string", "maxLength": 4000,
                "description": "Compact JSON-encoded value; use null for remove.",
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
        "required": [
            "answer", "answer_type", "based_on", "parent_analysis_run_id",
            "evidence_refs", "proposal",
        ],
        "properties": {
            "answer": {"type": "string", "maxLength": 240},
            "answer_type": {"enum": ["PROPOSAL"]},
            "based_on": {"enum": ["PROPOSAL_DRAFT"]},
            "parent_analysis_run_id": {"type": "string", "format": "uuid"},
            "evidence_refs": {
                "type": "array", "minItems": 1, "maxItems": 12,
                "items": {
                    "type": "object", "required": ["evidence_id"],
                    "properties": {"evidence_id": {"type": "string", "format": "uuid"}},
                },
            },
            "proposal": proposal,
        },
    }
    return {
        "prompt_key": "analysis-chat-governed-change",
        "semantic_version": "1.3.0",
        "system_template": (
            "You are the governed Scalpyn configuration action planner. Answer in the question "
            "language and return exactly one typed operation. The backend applies it only after a "
            "second explicit human confirmation. Use UPDATE_PROFILE_CONFIG for one profile, "
            "UPDATE_PROFILE_CONFIG_SET for multiple profiles, UPDATE_CONFIG_PROFILE for one global "
            "configuration family, and SET_PROFILE_ACTIVE_STATUS only for activation status. For a "
            "multi-profile operation, put each owned UUID once in target.profile_ids. When the exact "
            "same path, value, reason and evidence apply to several target profiles, emit one change: "
            "set profile_id and profile_name to null and put their zero-based target.profile_ids "
            "positions in profile_indexes. For a profile-specific change, set its profile_id and "
            "profile_name and use an empty profile_indexes array. For non-profile operations always "
            "use an empty profile_indexes array. Never repeat identical changes per profile. Use only "
            "actual paths and values supported by supplied evidence. Every change must cite supplied "
            "evidence IDs. Never invent IDs, fields, values, or evidence. Orders, secrets, arbitrary "
            "SQL/code, ML promotion, deletion, and runtime-gate modification are outside this contract."
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
    bind = op.get_bind()
    approved_at = datetime(2026, 8, 13, tzinfo=timezone.utc)
    prompt = _prompt_content()
    bind.execute(sa.text("""
        INSERT INTO ai_prompt_versions (
            id,prompt_key,semantic_version,system_template,user_template,
            input_schema_json,output_schema_json,tool_policy_json,
            provider_constraints_json,status,content_hash,created_at,approved_at
        ) VALUES (
            CAST(:id AS uuid),:prompt_key,:semantic_version,:system_template,:user_template,
            CAST(:input_schema_json AS jsonb),CAST(:output_schema_json AS jsonb),
            CAST(:tool_policy_json AS jsonb),CAST(:provider_constraints_json AS jsonb),
            'APPROVED',:content_hash,:created_at,:approved_at
        ) ON CONFLICT (prompt_key,semantic_version) DO NOTHING
    """), {
        "id": str(uuid.uuid5(PROMPT_NAMESPACE, "analysis-chat-governed-change@1.3.0")),
        **prompt,
        "input_schema_json": json.dumps(prompt["input_schema_json"]),
        "output_schema_json": json.dumps(prompt["output_schema_json"]),
        "tool_policy_json": json.dumps(prompt["tool_policy_json"]),
        "provider_constraints_json": json.dumps(prompt["provider_constraints_json"]),
        "content_hash": _canonical_hash(prompt),
        "created_at": approved_at,
        "approved_at": approved_at,
    })
    bind.execute(sa.text("""
        UPDATE config_profiles
           SET config_json = jsonb_set(
                   COALESCE(config_json, '{}'::jsonb),
                   '{proposal_max_output_tokens}', '16384'::jsonb, true
               ), updated_at = CURRENT_TIMESTAMP
         WHERE config_type = 'ai_analysis_chat_runtime'
           AND COALESCE((config_json->>'proposal_max_output_tokens')::integer, 0) < 16384
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE config_profiles
           SET config_json = jsonb_set(config_json, '{proposal_max_output_tokens}', '8192'::jsonb),
               updated_at = CURRENT_TIMESTAMP
         WHERE config_type = 'ai_analysis_chat_runtime'
           AND (config_json->>'proposal_max_output_tokens')::integer = 16384
    """))
    bind.execute(sa.text("""
        DELETE FROM ai_prompt_versions
         WHERE prompt_key = 'analysis-chat-governed-change' AND semantic_version = '1.3.0'
    """))
