"""Add bulk profile status actions and proposal output allowance.

Revision ID: 164_chat_bulk_profile_actions
Revises: 163_chat_governed_actions
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "164_chat_bulk_profile_actions"
down_revision = "163_chat_governed_actions"
branch_labels = None
depends_on = None

PROMPT_NAMESPACE = uuid.UUID("809e4f74-e34b-54d9-b611-4ee53a33198f")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")).hexdigest()


def _prompt_content() -> dict:
    target = {
        "type": "object",
        "required": [
            "profile_id", "profile_name", "config_type", "pool_id", "profile_ids",
        ],
        "properties": {
            "profile_id": {"type": ["string", "null"]},
            "profile_name": {"type": ["string", "null"]},
            "config_type": {"type": ["string", "null"]},
            "pool_id": {"type": ["string", "null"]},
            "profile_ids": {
                "type": "array",
                "maxItems": 32,
                "items": {"type": "string", "format": "uuid"},
            },
        },
    }
    change = {
        "type": "object",
        "required": [
            "op", "path", "value_json", "reason", "evidence_refs",
            "profile_id", "profile_name",
        ],
        "properties": {
            "op": {"enum": ["add", "replace", "remove"]},
            "path": {"type": "string", "minLength": 2, "maxLength": 500},
            "value_json": {
                "type": "string",
                "description": "Compact JSON-encoded value; use null for remove.",
                "maxLength": 4000,
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 500},
            "evidence_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {"type": "string", "format": "uuid"},
            },
            "profile_id": {"type": ["string", "null"]},
            "profile_name": {"type": ["string", "null"]},
        },
    }
    proposal = {
        "type": "object",
        "required": ["operation_type", "target", "objective", "risk", "changes"],
        "properties": {
            "operation_type": {
                "enum": [
                    "UPDATE_PROFILE_CONFIG",
                    "UPDATE_CONFIG_PROFILE",
                    "SET_PROFILE_ACTIVE_STATUS",
                ],
            },
            "target": target,
            "objective": {"type": "string", "minLength": 1, "maxLength": 500},
            "risk": {"type": "string", "minLength": 1, "maxLength": 500},
            "changes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": change,
            },
        },
    }
    output = {
        "type": "object",
        "required": [
            "answer", "answer_type", "based_on", "parent_analysis_run_id",
            "evidence_refs", "proposal",
        ],
        "properties": {
            "answer": {"type": "string", "maxLength": 500},
            "answer_type": {"enum": ["PROPOSAL"]},
            "based_on": {"enum": ["PROPOSAL_DRAFT"]},
            "parent_analysis_run_id": {"type": "string", "format": "uuid"},
            "evidence_refs": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "required": ["evidence_id"],
                    "properties": {
                        "evidence_id": {"type": "string", "format": "uuid"},
                    },
                },
            },
            "proposal": proposal,
        },
    }
    return {
        "prompt_key": "analysis-chat-governed-change",
        "semantic_version": "1.1.0",
        "system_template": (
            "You are the governed Scalpyn configuration action planner. Answer in the question "
            "language. Convert the explicitly confirmed request into exactly one typed operation. "
            "The backend applies it only after a second explicit human confirmation. Use "
            "UPDATE_PROFILE_CONFIG for fields inside one profile, UPDATE_CONFIG_PROFILE for one "
            "configuration family, and SET_PROFILE_ACTIVE_STATUS to activate or deactivate one or "
            "more existing profiles without deleting them. For SET_PROFILE_ACTIVE_STATUS, put every "
            "owned profile UUID in target.profile_ids and emit exactly one replace /is_active change "
            "per profile with matching profile_id and profile_name. Keep the response compact. Every "
            "change must cite supplied evidence IDs. Never invent IDs, values, or evidence. Orders, "
            "credentials, secrets, arbitrary SQL/code, ML promotion, deletion, and runtime-gate "
            "self-modification are outside this contract. Return JSON matching the schema."
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
        "provider_constraints_json": {
            "structured_output": True,
            "authority": "PROPOSAL_ONLY",
        },
    }


def upgrade() -> None:
    bind = op.get_bind()
    approved_at = datetime(2026, 8, 12, tzinfo=timezone.utc)
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
        "id": str(uuid.uuid5(
            PROMPT_NAMESPACE, "analysis-chat-governed-change@1.1.0"
        )),
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
                   '{proposal_max_output_tokens}',
                   '8192'::jsonb,
                   true
               ),
               updated_at = CURRENT_TIMESTAMP
         WHERE config_type = 'ai_analysis_chat_runtime'
           AND NOT COALESCE(config_json, '{}'::jsonb) ? 'proposal_max_output_tokens'
    """))


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE config_profiles
           SET config_json = COALESCE(config_json, '{}'::jsonb)
                             - 'proposal_max_output_tokens',
               updated_at = CURRENT_TIMESTAMP
         WHERE config_type = 'ai_analysis_chat_runtime'
    """))
    bind.execute(sa.text(
        "DELETE FROM ai_prompt_versions "
        "WHERE prompt_key='analysis-chat-governed-change' AND semantic_version='1.1.0'"
    ))
