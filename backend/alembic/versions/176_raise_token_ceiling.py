"""Raise the per-request token ceiling for cross-module evidence-heavy runs.

Revision ID: 176_raise_token_ceiling
Revises: 175_analysis_profile_cost_cap

Confirmed live 2026-08-17: a Shadow Portfolio "Causa raiz" run over 51
trades pulled ml_model_registry as cross-module dependency evidence (now
returning real data since the join fix) and needed
estimated_input_tokens=753143 -- comfortably past the 442300 ceiling
175 had just aligned the cost cap to. estimated_input_tokens is measured as
raw UTF-8 byte length of the assembled prompt (see systemic_langgraph_bridge.py),
not a real tokenizer count, so it runs 3-4x hotter than actual Anthropic
billing for JSON-heavy evidence -- the dollar caps below are sized against
this conservative byte count, not real expected spend.

Raises ai_analysis_profiles.request_token_limit 444600 -> 1200000 (the field
that also seeds a brand new AIBudgetPolicyRecord row's request_token_limit
the first time an origin_module is used) and recomputes max_cost_usd at
that new ceiling for all 9 profiles. Also raises request_token_limit on the
AIBudgetPolicyRecord rows already created for previously-used modules,
since those already exist and won't re-seed from the profile.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "176_raise_token_ceiling"
down_revision = "175_analysis_profile_cost_cap"
branch_labels = None
depends_on = None

PROFILE_SLUGS = (
    "systemic-overview", "systemic-overview-sonnet-5", "systemic-overview-opus-5",
    "root-cause", "root-cause-sonnet-5", "root-cause-opus-5",
    "risk-anomalies", "risk-anomalies-sonnet-5", "risk-anomalies-opus-5",
)

OLD_REQUEST_TOKEN_LIMIT = 444_600
NEW_REQUEST_TOKEN_LIMIT = 1_200_000
# ck_ai_analysis_profile_daily_budget requires daily >= request_token_limit,
# ck_ai_analysis_profile_monthly_budget requires monthly >= daily -- keep the
# original 5x daily->monthly ratio (4446000 / 889200 == 5.0 exactly).
OLD_DAILY_TOKEN_LIMIT = 889_200
NEW_DAILY_TOKEN_LIMIT = 1_200_000
OLD_MONTHLY_TOKEN_LIMIT = 4_446_000
NEW_MONTHLY_TOKEN_LIMIT = 6_000_000
MAX_OUTPUT_TOKENS = 2_300

NEW_MAX_COST_USD = {
    "claude-haiku-4-5-20251001": "1.25000000",
    "claude-sonnet-5": "2.50000000",
    "claude-opus-5": "6.50000000",
}
OLD_MAX_COST_USD = {
    "claude-haiku-4-5-20251001": "0.50000000",
    "claude-sonnet-5": "1.00000000",
    "claude-opus-5": "2.50000000",
}


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot(row: dict) -> dict:
    return {
        "slug": row["slug"],
        "name": row["name"],
        "description": row["description"],
        "provider": row["provider"],
        "model": row["model"],
        "analysis_mode": row["analysis_mode"],
        "authority": row["authority"],
        "question_template": row["question_template"],
        "max_cost_usd": str(row["max_cost_usd"]),
        "input_cost_per_million": str(row["input_cost_per_million"]),
        "output_cost_per_million": str(row["output_cost_per_million"]),
        "max_input_tokens": row["max_input_tokens"],
        "max_output_tokens": row["max_output_tokens"],
        "request_token_limit": row["request_token_limit"],
        "daily_token_limit": row["daily_token_limit"],
        "monthly_token_limit": row["monthly_token_limit"],
        "pricing_source_url": row["pricing_source_url"],
        "pricing_observed_at": row["pricing_observed_at"].isoformat(),
        "pricing_valid_until": row["pricing_valid_until"].isoformat(),
        "profile_version": row["profile_version"],
    }


def _apply_profiles(*, reverse: bool) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT slug, name, description, provider, model, analysis_mode, authority,
               question_template, max_cost_usd, input_cost_per_million,
               output_cost_per_million, max_input_tokens, max_output_tokens,
               request_token_limit, daily_token_limit, monthly_token_limit,
               pricing_source_url, pricing_observed_at, pricing_valid_until,
               profile_version
          FROM ai_analysis_profiles
         WHERE slug IN :slugs AND is_active IS TRUE
         ORDER BY slug
    """).bindparams(sa.bindparam("slugs", expanding=True)), {"slugs": list(PROFILE_SLUGS)}).mappings().all()
    if len(rows) != len(PROFILE_SLUGS):
        raise RuntimeError("ACTIVE_ANALYSIS_PROFILES_INCOMPLETE")

    for row in rows:
        row = dict(row)
        target_cost = OLD_MAX_COST_USD[row["model"]] if reverse else NEW_MAX_COST_USD[row["model"]]
        target_request_limit = OLD_REQUEST_TOKEN_LIMIT if reverse else NEW_REQUEST_TOKEN_LIMIT
        input_tokens_for_check = target_request_limit - MAX_OUTPUT_TOKENS
        worst_case = (
            input_tokens_for_check * Decimal(row["input_cost_per_million"])
            + int(row["max_output_tokens"]) * Decimal(row["output_cost_per_million"])
        ) / Decimal("1000000")
        if not reverse and worst_case > Decimal(target_cost):
            raise RuntimeError(f"ANALYSIS_PROFILE_COST_CAP_STILL_BELOW_WORST_CASE:{row['slug']}")

        profile_version = int(row["profile_version"]) + (1 if not reverse else -1)
        if profile_version < 1:
            raise RuntimeError("ANALYSIS_PROFILE_VERSION_INVALID")
        row["max_cost_usd"] = target_cost
        row["request_token_limit"] = target_request_limit
        row["daily_token_limit"] = OLD_DAILY_TOKEN_LIMIT if reverse else NEW_DAILY_TOKEN_LIMIT
        row["monthly_token_limit"] = OLD_MONTHLY_TOKEN_LIMIT if reverse else NEW_MONTHLY_TOKEN_LIMIT
        row["profile_version"] = profile_version
        snapshot = _snapshot(row)
        bind.execute(sa.text("""
            UPDATE ai_analysis_profiles
               SET max_cost_usd = :max_cost_usd,
                   request_token_limit = :request_token_limit,
                   daily_token_limit = :daily_token_limit,
                   monthly_token_limit = :monthly_token_limit,
                   profile_version = :profile_version,
                   profile_hash = :profile_hash,
                   updated_at = NOW()
             WHERE slug = :slug AND is_active IS TRUE
        """), {
            "slug": row["slug"],
            "max_cost_usd": target_cost,
            "request_token_limit": target_request_limit,
            "daily_token_limit": row["daily_token_limit"],
            "monthly_token_limit": row["monthly_token_limit"],
            "profile_version": profile_version,
            "profile_hash": _canonical_hash(snapshot),
        })


def _apply_budget_policies(*, reverse: bool) -> None:
    bind = op.get_bind()
    target = OLD_REQUEST_TOKEN_LIMIT if reverse else NEW_REQUEST_TOKEN_LIMIT
    bind.execute(sa.text("""
        UPDATE ai_budget_policies
           SET request_token_limit = :target
         WHERE is_active IS TRUE
    """), {"target": target})


def upgrade() -> None:
    _apply_profiles(reverse=False)
    _apply_budget_policies(reverse=False)


def downgrade() -> None:
    _apply_profiles(reverse=True)
    _apply_budget_policies(reverse=True)
