"""Bound systemic output and raise the governed response allowance.

Revision ID: 160_systemic_output_budget
Revises: 159_chat_prompt_repair
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa


revision = "160_systemic_output_budget"
down_revision = "159_chat_prompt_repair"
branch_labels = None
depends_on = None

PROMPT_NAMESPACE = uuid.UUID("809e4f74-e34b-54d9-b611-4ee53a33198f")
PROFILE_SLUGS = ("systemic-overview", "root-cause", "risk-anomalies")
GOVERNED_MAX_COST_USD = "0.45000000"
GOVERNED_MAX_INPUT_TOKENS = 200000
GOVERNED_REQUEST_TOKEN_LIMIT = 444600
GOVERNED_DAILY_TOKEN_LIMIT = 889200
GOVERNED_MONTHLY_TOKEN_LIMIT = 4446000
SYSTEM_TEMPLATE = (
    "You are Scalpyn systemic analysis. Use only canonical typed-tool evidence. "
    "Never invent metrics, causal claims, authority, or missing values. "
    "Global Risk and Strategies are hard vetoes; ML, Social Score, and Market Regime are read-only. "
    "Return every required contract field in one compact JSON object."
)
USER_TEMPLATE = (
    "Question: {question}\nCanonical typed-tool evidence: {dataset}\n"
    "Configuration bundle: {configuration}\n"
    "Return minified JSON without markdown or repeated context. diagnosis must be non-empty and at "
    "most 240 characters; root_cause_classification must be non-empty and at most 96; "
    "affected_modules has at most 6 items. Select at most 7 decision-relevant evidence objects from "
    "the supplied typed tools, using only evidence_id, tool, and a finding of at most 160 characters. "
    "The complete typed-tool audit remains outside this response. data_quality and market_regime are "
    "compact objects with at most 4 keys. memory_hits and discarded_hypotheses have at most 2 objects "
    "and 3 keys per object. recommendations has at most 1 item and may be empty when no safe action is "
    "supported. Every recommendation requires target_module, target_path, operation, side_effect_class, "
    "confidence from 0 to 1, risk_conflicts, strategy_conflicts, validation_plan, and rollback_plan; "
    "LIVE_WRITE is forbidden. warnings and limitations have at most 2 strings of at most 160 characters "
    "each. Use no more than 3 keys in nested plan, conflict, impact, or memory objects."
)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bounded_output_schema(value: dict) -> dict:
    schema = json.loads(json.dumps(value))
    properties = schema["properties"]
    properties["diagnosis"].update({"minLength": 1, "maxLength": 240})
    properties["root_cause_classification"].update({"minLength": 1, "maxLength": 96})
    properties["affected_modules"].update({
        "maxItems": 6,
        "items": {"type": "string", "maxLength": 64},
    })
    properties["evidence"].update({"minItems": 1, "maxItems": 7})
    for field, maximum in (("evidence_id", 64), ("tool", 96), ("finding", 160)):
        properties["evidence"]["items"]["properties"][field]["maxLength"] = maximum
    for field in ("discarded_hypotheses", "memory_hits"):
        properties[field].update({
            "maxItems": 2,
            "items": {"type": "object", "maxProperties": 3},
        })
    for field in ("data_quality", "market_regime"):
        properties[field]["maxProperties"] = 4
    recommendation = properties["recommendations"]
    recommendation["maxItems"] = 1
    recommendation_properties = recommendation["items"]["properties"]
    recommendation_properties["confidence"].update({"minimum": 0, "maximum": 1})
    for field, maximum in (
        ("target_module", 64),
        ("target_entity_id", 96),
        ("target_path", 128),
        ("operation", 64),
    ):
        recommendation_properties[field]["maxLength"] = maximum
    for field in ("risk_conflicts", "strategy_conflicts"):
        recommendation_properties[field].update({
            "maxItems": 2,
            "items": {"type": "object", "maxProperties": 3},
        })
    for field in ("expected_impact", "validation_plan", "rollback_plan"):
        recommendation_properties[field]["maxProperties"] = 3
    for field in ("warnings", "limitations"):
        properties[field].update({
            "maxItems": 2,
            "items": {"type": "string", "maxLength": 160},
        })
    return schema


def _profile_snapshot(
    row: dict,
    *,
    max_output_tokens: int,
    profile_version: int,
    enforce_governed_budget: bool,
) -> dict:
    max_cost_usd = GOVERNED_MAX_COST_USD if enforce_governed_budget else str(row["max_cost_usd"])
    max_input_tokens = (
        GOVERNED_MAX_INPUT_TOKENS if enforce_governed_budget else row["max_input_tokens"]
    )
    request_token_limit = (
        GOVERNED_REQUEST_TOKEN_LIMIT if enforce_governed_budget else row["request_token_limit"]
    )
    daily_token_limit = (
        GOVERNED_DAILY_TOKEN_LIMIT if enforce_governed_budget else row["daily_token_limit"]
    )
    monthly_token_limit = (
        GOVERNED_MONTHLY_TOKEN_LIMIT if enforce_governed_budget else row["monthly_token_limit"]
    )
    return {
        "slug": row["slug"],
        "name": row["name"],
        "description": row["description"],
        "provider": row["provider"],
        "model": row["model"],
        "analysis_mode": row["analysis_mode"],
        "authority": row["authority"],
        "question_template": row["question_template"],
        "max_cost_usd": max_cost_usd,
        "input_cost_per_million": str(row["input_cost_per_million"]),
        "output_cost_per_million": str(row["output_cost_per_million"]),
        "max_input_tokens": max_input_tokens,
        "max_output_tokens": max_output_tokens,
        "request_token_limit": request_token_limit,
        "daily_token_limit": daily_token_limit,
        "monthly_token_limit": monthly_token_limit,
        "pricing_source_url": row["pricing_source_url"],
        "pricing_observed_at": row["pricing_observed_at"].isoformat(),
        "pricing_valid_until": row["pricing_valid_until"].isoformat(),
        "profile_version": profile_version,
    }


def _update_profiles(
    *,
    max_output_tokens: int,
    version_delta: int,
    enforce_governed_budget: bool,
) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT slug,name,description,provider,model,analysis_mode,authority,
               question_template,max_cost_usd,input_cost_per_million,
               output_cost_per_million,max_input_tokens,max_output_tokens,
               request_token_limit,daily_token_limit,monthly_token_limit,
               pricing_source_url,pricing_observed_at,pricing_valid_until,
               profile_version
          FROM ai_analysis_profiles
         WHERE slug IN ('systemic-overview','root-cause','risk-anomalies')
           AND is_active IS TRUE
         ORDER BY slug
    """)).mappings().all()
    if len(rows) != len(PROFILE_SLUGS):
        raise RuntimeError("ACTIVE_ANALYSIS_PROFILES_INCOMPLETE")
    for row in rows:
        current_matches_target = enforce_governed_budget and all((
            str(row["max_cost_usd"]) == GOVERNED_MAX_COST_USD,
            int(row["max_input_tokens"]) == GOVERNED_MAX_INPUT_TOKENS,
            int(row["max_output_tokens"]) == max_output_tokens,
            int(row["request_token_limit"]) == GOVERNED_REQUEST_TOKEN_LIMIT,
            int(row["daily_token_limit"]) == GOVERNED_DAILY_TOKEN_LIMIT,
            int(row["monthly_token_limit"]) == GOVERNED_MONTHLY_TOKEN_LIMIT,
        ))
        profile_version = int(row["profile_version"])
        if not current_matches_target:
            profile_version += version_delta
        if profile_version < 1:
            raise RuntimeError("ANALYSIS_PROFILE_VERSION_INVALID")
        snapshot = _profile_snapshot(
            dict(row),
            max_output_tokens=max_output_tokens,
            profile_version=profile_version,
            enforce_governed_budget=enforce_governed_budget,
        )
        worst_case_cost = (
            int(snapshot["max_input_tokens"]) * Decimal(snapshot["input_cost_per_million"])
            + max_output_tokens * Decimal(snapshot["output_cost_per_million"])
        ) / Decimal("1000000")
        if worst_case_cost > Decimal(snapshot["max_cost_usd"]):
            raise RuntimeError("ANALYSIS_PROFILE_COST_CAP_EXCEEDED")
        bind.execute(sa.text("""
            UPDATE ai_analysis_profiles
               SET max_cost_usd=:max_cost_usd,
                   max_input_tokens=:max_input_tokens,
                   max_output_tokens=:max_output_tokens,
                   request_token_limit=:request_token_limit,
                   daily_token_limit=:daily_token_limit,
                   monthly_token_limit=:monthly_token_limit,
                   profile_version=:profile_version,
                   profile_hash=:profile_hash,
                   updated_at=NOW()
             WHERE slug=:slug AND is_active IS TRUE
        """), {
            "slug": row["slug"],
            "max_cost_usd": snapshot["max_cost_usd"],
            "max_input_tokens": snapshot["max_input_tokens"],
            "max_output_tokens": max_output_tokens,
            "request_token_limit": snapshot["request_token_limit"],
            "daily_token_limit": snapshot["daily_token_limit"],
            "monthly_token_limit": snapshot["monthly_token_limit"],
            "profile_version": profile_version,
            "profile_hash": _canonical_hash(snapshot),
        })


def upgrade() -> None:
    bind = op.get_bind()
    previous = bind.execute(sa.text("""
        SELECT input_schema_json,output_schema_json,tool_policy_json,
               provider_constraints_json
          FROM ai_prompt_versions
         WHERE prompt_key='systemic-multimodule'
           AND semantic_version='2.0.4'
           AND status='APPROVED'
    """)).mappings().one()
    content = {
        "prompt_key": "systemic-multimodule",
        "semantic_version": "2.0.5",
        "system_template": SYSTEM_TEMPLATE,
        "user_template": USER_TEMPLATE,
        "input_schema_json": previous["input_schema_json"],
        "output_schema_json": _bounded_output_schema(previous["output_schema_json"]),
        "tool_policy_json": previous["tool_policy_json"],
        "provider_constraints_json": previous["provider_constraints_json"],
    }
    approved_at = datetime(2026, 8, 12, tzinfo=timezone.utc)
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
        "id": uuid.uuid5(PROMPT_NAMESPACE, "systemic-multimodule@2.0.5"),
        "prompt_key": content["prompt_key"],
        "semantic_version": content["semantic_version"],
        "system_template": content["system_template"],
        "user_template": content["user_template"],
        "input_schema_json": json.dumps(content["input_schema_json"]),
        "output_schema_json": json.dumps(content["output_schema_json"]),
        "tool_policy_json": json.dumps(content["tool_policy_json"]),
        "provider_constraints_json": json.dumps(content["provider_constraints_json"]),
        "content_hash": _canonical_hash(content),
        "created_at": approved_at,
        "approved_at": approved_at,
    })
    _update_profiles(
        max_output_tokens=2300,
        version_delta=1,
        enforce_governed_budget=True,
    )
    bind.execute(sa.text("""
        UPDATE ai_graph_runs AS run
           SET provider_transport_attempted=TRUE,
               error_kind='PROVIDER_OUTPUT_FAILED',
               last_error_safe_message='Provider returned an incomplete or invalid structured response',
               updated_at=NOW()
          FROM ai_budget_reservations AS reservation
         WHERE reservation.ai_request_id=run.ai_request_id
           AND reservation.tenant_id=run.tenant_id
           AND reservation.provider_transport_attempted IS TRUE
           AND run.status='FAILED'
           AND run.failed_node IN ('validate_output','validate_structured_output')
           AND run.last_error_code IN ('PROVIDER_OUTPUT_TRUNCATED','PROVIDER_OUTPUT_JSON_INVALID','OUTPUT_SCHEMA_INVALID')
    """))


def downgrade() -> None:
    _update_profiles(
        max_output_tokens=1350,
        version_delta=-1,
        enforce_governed_budget=False,
    )
    op.execute(sa.text(
        "DELETE FROM ai_prompt_versions "
        "WHERE prompt_key='systemic-multimodule' AND semantic_version='2.0.5'"
    ))
