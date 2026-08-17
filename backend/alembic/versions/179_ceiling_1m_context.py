"""Correct the Sonnet 5 / Opus 5 context window in ai_analysis_profiles.

Revision ID: 179_ceiling_1m_context
Revises: 178_ceiling_empirical

176/177/178 all derived request_token_limit from a max_input_tokens value
of 200_000 for every Anthropic model in provider_registry.py's
default_registry(). That 200_000 figure was correct for
claude-haiku-4-5-20251001 (its real Anthropic context window), but was
never a real limit for claude-sonnet-5 or claude-opus-5 -- both have a
1,000,000-token context window per Anthropic's own model documentation.
provider_registry.py is corrected in the same change that introduces this
migration.

This migration raises max_input_tokens (and the request/daily/monthly
budget chain derived from it, plus max_cost_usd to cover the larger
worst-case request) for the 6 profiles that use claude-sonnet-5 or
claude-opus-5. The 3 claude-haiku-4-5-20251001 profiles are untouched --
200_000 remains their real ceiling.

request_token_limit is set to the CHECK-constraint minimum
(max_input_tokens + max_output_tokens = 1_000_000 + 2_300 = 1_002_300)
rather than something larger, because request_token_limit is compared
against a raw UTF-8 byte-length proxy for token count elsewhere in the
pipeline (systemic_langgraph_bridge.py's estimated_input_tokens), and that
proxy overestimates real token count by roughly 2x for this evidence
shape (see 178's own docstring). A byte count under 1_002_300 therefore
implies a real token count nowhere near the actual 1,000,000-token model
ceiling -- there is no need to also loosen the byte-based gate to make
use of the corrected model capacity.

daily/monthly keep the 1x/5x ratio to request_token_limit established by
176/177/178.

Also updates the one matching ai_budget_policies row
(provider=anthropic, model=claude-opus-5, module=shadow_portfolio) so the
module-level budget gate doesn't independently reclamp requests back to
400_000 after the profile-level ceiling is raised. No sonnet-5 row exists
in ai_budget_policies today, so there is nothing to update there.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "179_ceiling_1m_context"
down_revision = "178_ceiling_empirical"
branch_labels = None
depends_on = None

PROFILE_SLUGS = (
    "systemic-overview-sonnet-5", "systemic-overview-opus-5",
    "root-cause-sonnet-5", "root-cause-opus-5",
    "risk-anomalies-sonnet-5", "risk-anomalies-opus-5",
)

OLD_MAX_INPUT_TOKENS = 200_000
NEW_MAX_INPUT_TOKENS = 1_000_000
MAX_OUTPUT_TOKENS = 2_300

OLD_REQUEST_TOKEN_LIMIT = 400_000
NEW_REQUEST_TOKEN_LIMIT = NEW_MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS  # 1_002_300
OLD_DAILY_TOKEN_LIMIT = 400_000
NEW_DAILY_TOKEN_LIMIT = NEW_REQUEST_TOKEN_LIMIT
OLD_MONTHLY_TOKEN_LIMIT = 2_000_000
NEW_MONTHLY_TOKEN_LIMIT = NEW_DAILY_TOKEN_LIMIT * 5

NEW_MAX_COST_USD = {
    "claude-sonnet-5": "2.10000000",
    "claude-opus-5": "5.10000000",
}
OLD_MAX_COST_USD = {
    "claude-sonnet-5": "0.90000000",
    "claude-opus-5": "2.25000000",
}

BUDGET_POLICY_PROVIDER = "anthropic"
BUDGET_POLICY_MODEL = "claude-opus-5"
BUDGET_POLICY_MODULE = "shadow_portfolio"
OLD_BUDGET_POLICY_DAILY = 480_000
NEW_BUDGET_POLICY_DAILY = NEW_DAILY_TOKEN_LIMIT
OLD_BUDGET_POLICY_MONTHLY = 2_400_000
NEW_BUDGET_POLICY_MONTHLY = NEW_MONTHLY_TOKEN_LIMIT
OLD_BUDGET_POLICY_REQUEST = 400_000
NEW_BUDGET_POLICY_REQUEST = NEW_REQUEST_TOKEN_LIMIT


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
        if int(row["max_input_tokens"]) != (OLD_MAX_INPUT_TOKENS if not reverse else NEW_MAX_INPUT_TOKENS):
            raise RuntimeError(f"UNEXPECTED_CURRENT_MAX_INPUT_TOKENS:{row['slug']}: {row['max_input_tokens']}")

        target_cost = OLD_MAX_COST_USD[row["model"]] if reverse else NEW_MAX_COST_USD[row["model"]]
        target_max_input = OLD_MAX_INPUT_TOKENS if reverse else NEW_MAX_INPUT_TOKENS
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
        row["max_input_tokens"] = target_max_input
        row["request_token_limit"] = target_request_limit
        row["daily_token_limit"] = OLD_DAILY_TOKEN_LIMIT if reverse else NEW_DAILY_TOKEN_LIMIT
        row["monthly_token_limit"] = OLD_MONTHLY_TOKEN_LIMIT if reverse else NEW_MONTHLY_TOKEN_LIMIT
        row["profile_version"] = profile_version
        snapshot = _snapshot(row)
        bind.execute(sa.text("""
            UPDATE ai_analysis_profiles
               SET max_cost_usd = :max_cost_usd,
                   max_input_tokens = :max_input_tokens,
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
            "max_input_tokens": target_max_input,
            "request_token_limit": target_request_limit,
            "daily_token_limit": row["daily_token_limit"],
            "monthly_token_limit": row["monthly_token_limit"],
            "profile_version": profile_version,
            "profile_hash": _canonical_hash(snapshot),
        })


def _apply_budget_policy(*, reverse: bool) -> None:
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE ai_budget_policies
           SET request_token_limit = :request_token_limit,
               daily_token_limit = :daily_token_limit,
               monthly_token_limit = :monthly_token_limit
         WHERE provider = :provider AND model = :model AND module = :module AND is_active IS TRUE
    """), {
        "provider": BUDGET_POLICY_PROVIDER,
        "model": BUDGET_POLICY_MODEL,
        "module": BUDGET_POLICY_MODULE,
        "request_token_limit": OLD_BUDGET_POLICY_REQUEST if reverse else NEW_BUDGET_POLICY_REQUEST,
        "daily_token_limit": OLD_BUDGET_POLICY_DAILY if reverse else NEW_BUDGET_POLICY_DAILY,
        "monthly_token_limit": OLD_BUDGET_POLICY_MONTHLY if reverse else NEW_BUDGET_POLICY_MONTHLY,
    })


def upgrade() -> None:
    _apply_profiles(reverse=False)
    _apply_budget_policy(reverse=False)


def downgrade() -> None:
    _apply_profiles(reverse=True)
    _apply_budget_policy(reverse=True)
