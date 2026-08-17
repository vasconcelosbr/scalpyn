"""Recalibrate the byte-based reservation ceiling against the real 200k-token
context window instead of a single observed byte count.

Revision ID: 177_recalibrate_ceiling
Revises: 176_raise_token_ceiling

176 raised request_token_limit 444600 -> 1200000 purely to admit the one
observed case (estimated_input_tokens=753143) that had just tripped
AI_INPUT_RESERVATION_EXCEEDED. estimated_input_tokens is raw UTF-8 byte
length, not a real tokenizer count (systemic_langgraph_bridge.py), and 176's
own docstring already flagged it runs "3-4x hotter than actual Anthropic
billing" -- but the new ceiling was never checked against
ai_analysis_profiles.max_input_tokens (200000, the real per-profile limit
already in the schema and validated against the provider catalog in
ai_modules.py). It also was never checked against Anthropic's own advertised
context window.

Confirmed live 2026-08-17: run 873d3686-ff22-4c81-8c88-3ae7a2378674
(estimated_input_tokens=752677, comfortably inside the new 1200000 ceiling)
passed the reservation gate and then failed transport with HTTP 400 from
`POST https://api.anthropic.com/v1/messages` -- the request the ceiling now
admits is one Anthropic itself rejects.

Empirical byte:token ratio, computed from 30+ RECONCILED ai_budget_reservations
rows with both estimated_input_tokens (bytes) and actual_tokens (real,
provider-billed) for a single clean pass (excluding retried/schema-repaired
rows, which double-count tokens across attempts): ranges 2.44-2.59, i.e.
notably gentler than 176's "3-4x" guess. Using the conservative (lowest
observed, worst-compression) ratio of 2.4:

    request_token_limit = max_input_tokens (200000) * 2.4 = 480000

This keeps every admitted request's *real* token count under the actual
200000-token context window with margin, even at the worst historically
observed compression ratio -- not just under an arbitrary byte count picked
to fit one case. It is deliberately NOT a full fix for the underlying
problem (a Shadow Portfolio root-cause-audit run with heavy cross-module
ml_model_registry evidence genuinely needs more input than the model
supports): that requires shrinking the evidence assembled into the prompt,
not moving this ceiling. This migration only makes the failure mode
predictable again (AI_INPUT_RESERVATION_EXCEEDED, before any provider
call is made and billed) instead of a paid, opaque HTTP 400 after transport.

daily_token_limit is kept equal to request_token_limit and
monthly_token_limit at the same 5x ratio 176 preserved
(2400000 / 480000 == 5.0), and max_cost_usd is recomputed at the new,
lower worst-case input volume -- which happens to land back on 176's own
OLD_MAX_COST_USD values (0.50 / 1.00 / 2.50), reused here rather than
re-derived, since they already satisfy the worst-case check at this ceiling.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "177_recalibrate_ceiling"
down_revision = "176_raise_token_ceiling"
branch_labels = None
depends_on = None

PROFILE_SLUGS = (
    "systemic-overview", "systemic-overview-sonnet-5", "systemic-overview-opus-5",
    "root-cause", "root-cause-sonnet-5", "root-cause-opus-5",
    "risk-anomalies", "risk-anomalies-sonnet-5", "risk-anomalies-opus-5",
)

OLD_REQUEST_TOKEN_LIMIT = 1_200_000
NEW_REQUEST_TOKEN_LIMIT = 480_000
# ck_ai_analysis_profile_daily_budget requires daily >= request_token_limit,
# ck_ai_analysis_profile_monthly_budget requires monthly >= daily -- keep the
# 5x daily->monthly ratio 176 established (2400000 / 480000 == 5.0 exactly).
OLD_DAILY_TOKEN_LIMIT = 1_200_000
NEW_DAILY_TOKEN_LIMIT = 480_000
OLD_MONTHLY_TOKEN_LIMIT = 6_000_000
NEW_MONTHLY_TOKEN_LIMIT = 2_400_000
MAX_OUTPUT_TOKENS = 2_300

NEW_MAX_COST_USD = {
    "claude-haiku-4-5-20251001": "0.50000000",
    "claude-sonnet-5": "1.00000000",
    "claude-opus-5": "2.50000000",
}
OLD_MAX_COST_USD = {
    "claude-haiku-4-5-20251001": "1.25000000",
    "claude-sonnet-5": "2.50000000",
    "claude-opus-5": "6.50000000",
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
        if worst_case > Decimal(target_cost):
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
