"""Raise the ``answer`` field's maxLength so a LIMITATION refusal can carry a
real explanation (FIX-AC-OBS-004_r2 Fase C).

Root cause, confirmed by 10/10 deterministic reproductions against the real
provider (6.2K-token and 120K-token context, both scales, byte-identical
output every time): the model correctly emits ``answer_type=LIMITATION`` and
``proposal=null`` when a request can't be executed, but the honest
explanation it writes is consistently 300-400+ chars -- well past the
``answer`` cap. The cap regressed across versions: 2000 (1.0.0) -> 500
(1.1.0/1.2.0) -> 240 (1.3.0, unchanged through 1.7.0). This was misdiagnosed
in an earlier pass as an ``answer_type``/``proposal`` consistency bug; direct
``jsonschema.validate()`` against the captured raw output proved the failure
path is ``answer`` / ``maxLength`` alone -- ``proposal`` was already
correctly null.

This migration changes exactly one leaf value (``answer.maxLength``:
240 -> 2000, restoring the original 1.0.0 bound) on top of 169's cumulative
contract (evidence labels + score rule_id). No other field changes.

Revision ID: 170_chat_answer_maxlen
Revises: 169_chat_score_rule_id
Create Date: 2026-08-16

NOTE: the first attempt at this migration used revision id
"170_chat_limitation_answer_length" (34 chars), exceeding
alembic_version.version_num's varchar(32) limit -- StringDataRightTruncationError
on the final UPDATE, crash-looping the API service after 168/169 had already
applied. Renamed to fit under 32 chars; no schema/data impact from the rename.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "170_chat_answer_maxlen"
down_revision = "169_chat_score_rule_id"
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
            "op", "value_json", "reason", "evidence_refs", "profile_id",
            "profile_name", "profile_indexes",
        ],
        "properties": {
            "op": {"enum": ["add", "replace", "remove"]},
            "rule_id": {
                "type": ["string", "null"],
                "pattern": "^rule_[a-z0-9_]+$",
                "description": (
                    "Set ONLY to edit a persisted score.scoring_rules[].points: the exact "
                    "rule id copied verbatim from evidence (e.g. rule_rsi_between_68_78). "
                    "When set, omit path/old_value_json/array_guards_json -- the backend "
                    "derives them from the live document. Never invent a rule id."
                ),
            },
            "path": {
                "type": "string", "minLength": 2, "maxLength": 500,
                "description": (
                    "Concrete RFC 6901 pointer into the persisted target document; "
                    "every array segment is a zero-based decimal index. Omit when rule_id "
                    "is set."
                ),
            },
            "value_json": {
                "type": "string", "maxLength": 4000,
                "description": (
                    "Compact JSON-encoded value; encode JSON null for remove. When "
                    "rule_id is set, this is the new points value."
                ),
            },
            "old_value_json": {
                "type": "string", "maxLength": 4000,
                "description": (
                    "Compact JSON-encoded current leaf value copied from evidence; "
                    "encode JSON null only for add. Omit when rule_id is set."
                ),
            },
            "array_guards_json": {
                "type": "string", "maxLength": 4000,
                "description": (
                    "Compact JSON array with one {path,identity} guard for every indexed "
                    "array element traversed by path; encode [] when no array is traversed. "
                    "Omit when rule_id is set."
                ),
            },
            "reason": {"type": "string", "minLength": 1, "maxLength": 240},
            "evidence_refs": {
                "type": "array", "minItems": 1, "maxItems": 4,
                "items": {
                    "type": "string",
                    "pattern": "^E([1-9]|1[0-2])$",
                    "description": (
                        "A label from the Available evidence menu (E1..E12), never a "
                        "raw evidence_id or any invented identifier."
                    ),
                },
            },
            "profile_id": {"type": ["string", "null"]},
            "profile_name": {"type": ["string", "null"]},
            "profile_indexes": {
                "type": "array", "maxItems": 32,
                "items": {"type": "integer", "minimum": 0, "maximum": 31},
            },
        },
        "if": {
            "required": ["rule_id"],
            "properties": {"rule_id": {"type": "string"}},
        },
        "then": {},
        "else": {
            "required": ["path", "old_value_json", "array_guards_json"],
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
            # 240 -> 2000: a LIMITATION refusal needs room for a real
            # explanation (observed 300-400+ chars, 100% reproducible at
            # 240). 2000 restores the original 1.0.0 bound.
            "answer": {"type": "string", "maxLength": 2000},
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
        "semantic_version": "1.8.0",
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
            "and entry_triggers. scoring.weights is dead configuration -- accepted for legacy API "
            "compatibility only, never read by the real scoring computation -- and is rejected "
            "wherever proposed; never edit it. A grouped profile change is valid only when the exact "
            "concrete path exists with the same meaning in every selected profile; otherwise emit "
            "profile-specific changes with each actual index. To change how many points a global "
            "score rule is worth, do NOT construct a path. Instead set change.rule_id to the exact "
            "persisted score.scoring_rules[].id copied verbatim from evidence (e.g. "
            "rule_rsi_between_68_78) with value_json set to the new points, omit path/old_value_json/"
            "array_guards_json entirely, use UPDATE_CONFIG_PROFILE with target.config_type=score and "
            "target.pool_id=null, and op=replace; the backend resolves the real array index and old "
            "value. This is the ONLY supported way to change a scoring rule's points -- there is no "
            "scoring.rules path at the profile level and no scoring_rules key at any other path; "
            "never write /scoring/rules/<name> or any similar guessed path. Global score rules "
            "otherwise remain read-only through this contract; the score document's weights and "
            "thresholds keys are also outside this contract. UPDATE_CONFIG_PROFILE currently supports "
            "only the complete global score document with target.pool_id=null, and only through "
            "rule_id for scoring_rules edits. Spot, futures, risk, strategy and every other config "
            "family lack a complete governed semantic validator and must return LIMITATION. Exactly "
            "one proposal cannot mix global config and profile resources. Paths such as "
            "/scoring/rules/rsi_overbought_penalty, /filters/conditions/taker_ratio/value, "
            "/entry_triggers/conditions/adx_minimum and any unsupported /execution_policy profile "
            "root are invalid. If the request spans resource families, lacks an existing concrete "
            "path, or cannot fit one unambiguous operation, return answer_type=LIMITATION and "
            "proposal=null; explain concretely and completely what is missing or unsupported and "
            "what the user would need to supply or do instead -- you have room for a full "
            "explanation, do not truncate or omit reasoning to stay short; never guess, coerce, "
            "skip profiles, or invent schema. For identical "
            "concrete changes across profiles, group with profile_indexes; group only when path, old "
            "value, array identities and new value are all identical; otherwise set profile_id and "
            "profile_name and leave profile_indexes empty. Available evidence is listed under a short "
            "label per item (E1, E2, ...). Every change.evidence_refs entry MUST be one of those exact "
            "labels, copied verbatim; never cite a raw evidence_id, a tool name, or any label not "
            "present in the Available evidence list -- an unlisted or invented label is rejected "
            "before any other validation. Orders, secrets, arbitrary SQL/code, ML promotion, deletion "
            "and runtime-gate modification remain outside this contract."
        ),
        "user_template": (
            "Parent analysis: {parent_analysis}\n"
            "Available evidence (cite ONLY these labels in changes[].evidence_refs):\n"
            "{evidence_labels}\n"
            "Evidence detail: {evidence}\n"
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
    approved_at = datetime(2026, 8, 16, tzinfo=timezone.utc)
    prompt = _prompt_content()
    prompt_id = str(uuid.uuid5(
        PROMPT_NAMESPACE,
        "analysis-chat-governed-change@1.8.0",
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
        raise RuntimeError("ANALYSIS_CHAT_PROMPT_1_8_CONFLICT")


def downgrade() -> None:
    op.execute(sa.text("""
        UPDATE ai_prompt_versions
           SET status = 'DEPRECATED',
               deprecated_at = CURRENT_TIMESTAMP
         WHERE prompt_key = 'analysis-chat-governed-change'
           AND semantic_version = '1.8.0'
    """))
