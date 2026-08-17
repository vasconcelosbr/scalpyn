"""Recalibrate the byte-based reservation ceiling against the observed
success/failure boundary instead of an estimated ratio.

Revision ID: 178_ceiling_empirical
Revises: 177_recalibrate_ceiling

177 derived request_token_limit (480000) from max_input_tokens (200000)
times a byte:token ratio (2.4) measured empirically across a general
sample of RECONCILED reservations. Confirmed live 2026-08-17: that ratio
was too generous for the systemic-multimodule / ROOT_CAUSE_AUDIT prompt
shape specifically. Full history of that mode's estimated_input_tokens vs
outcome:

    753148, 752677  -- FAILED  (pre-177, blocked or transport-failed)
    412187          -- FAILED  (post-177, passed the 480000 gate, then
                                 HTTP 400 from Anthropic -- context length)
    399952          -- COMPLETED  (highest confirmed-good value)
    366345, 365743, 363493, 363438  -- COMPLETED

The real boundary sits between 399952 and 412187 bytes, i.e. a ratio of
~2.0-2.06 for this prompt shape, not 2.4. Recalibrating directly off the
observed boundary rather than re-deriving another estimated ratio:

    request_token_limit = 400000  (at the highest byte count ever
    confirmed to complete for this mode, ~3% below the lowest byte count
    ever confirmed to fail transport)

This is deliberately mode-agnostic (one ceiling for all profiles, as 176/
177 already established) rather than a special case for ROOT_CAUSE_AUDIT,
since the byte:token ratio is a property of typical evidence content
(JSON density), not of the prompt_key -- a SYSTEMIC or risk-anomalies run
assembling similarly dense cross-module evidence would hit the same real
wall. Once real per-request tokenizer counts replace the raw-byte proxy
(tracked separately -- see systemic_langgraph_bridge.py's own estimate
comment), this ceiling can be derived exactly instead of empirically.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "178_ceiling_empirical"
down_revision = "177_recalibrate_ceiling"
branch_labels = None
depends_on = None

PROFILE_SLUGS = (
    "systemic-overview", "systemic-overview-sonnet-5", "systemic-overview-opus-5",
    "root-cause", "root-cause-sonnet-5", "root-cause-opus-5",
    "risk-anomalies", "risk-anomalies-sonnet-5", "risk-anomalies-opus-5",
)

OLD_REQUEST_TOKEN_LIMIT = 480_000
NEW_REQUEST_TOKEN_LIMIT = 400_000
# Keep the 5x daily->monthly ratio 176/177 established.
OLD_DAILY_TOKEN_LIMIT = 480_000
NEW_DAILY_TOKEN_LIMIT = 400_000
OLD_MONTHLY_TOKEN_LIMIT = 2_400_000
NEW_MONTHLY_TOKEN_LIMIT = 2_000_000
MAX_OUTPUT_TOKENS = 2_300

NEW_MAX_COST_USD = {
    "claude-haiku-4-5-20251001": "0.45000000",
    "claude-sonnet-5": "0.90000000",
    "claude-opus-5": "2.25000000",
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
