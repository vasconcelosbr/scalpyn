"""Raise DeepSeek analysis profiles' max_output_tokens 2300 -> 4600.

Revision ID: 185_deepseek_output_ceiling
Revises: 184_deepseek_analysis_profiles

The first real DeepSeek Intelligence Run (module-analysis-profile,
2026-08-18 22:46 UTC, deepseek-v4-flash, systemic-overview/shadow_portfolio)
completed transport successfully -- confirmed by ai_usage_records: 318112
input tokens, exactly 2300 output tokens, real cost $0.0705 charged -- but
failed validate_output with PROVIDER_OUTPUT_TRUNCATED: tokens_output hit the
2300 cap exactly, so the model's structured JSON response never finished.
2300 was inherited from the Anthropic Sonnet-5/Opus-5 tier (173/179) and had
never actually been exercised against DeepSeek before this run.

Doubles max_output_tokens to 4600 per the account owner's explicit choice
(not a guess at DeepSeek's real behavior -- root cause of the extra token
usage, e.g. reasoning tokens vs Claude, is not confirmed). max_cost_usd is
NOT changed: because max_input_tokens (1,000,000) dominates the worst-case
formula so heavily, the extra 2300 output tokens only add ~$0.0005/$0.0015
to worst-case cost (flash/pro) -- both existing caps ($0.23/$0.67) already
cover the new worst case with margin (upgrade() asserts this and aborts if
a future edit invalidates the assumption, same guard pattern as 179).

Also updates the one ai_budget_policies row already auto-created by the
first real run (deepseek/deepseek-v4-flash/shadow_portfolio) so the
module-level gate doesn't independently reclamp back to the old ceiling --
mirrors 179's handling of the equivalent anthropic/opus-5/shadow_portfolio
row. No other (provider, model, module) budget policy rows exist yet for
deepseek.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "185_deepseek_output_ceiling"
down_revision = "184_deepseek_analysis_profiles"
branch_labels = None
depends_on = None

PROFILE_SLUGS = (
    "systemic-overview-deepseek-v4-flash", "systemic-overview-deepseek-v4-pro",
    "root-cause-deepseek-v4-flash", "root-cause-deepseek-v4-pro",
    "risk-anomalies-deepseek-v4-flash", "risk-anomalies-deepseek-v4-pro",
)

MAX_INPUT_TOKENS = 1_000_000
OLD_MAX_OUTPUT_TOKENS = 2_300
NEW_MAX_OUTPUT_TOKENS = 4_600

OLD_REQUEST_TOKEN_LIMIT = MAX_INPUT_TOKENS + OLD_MAX_OUTPUT_TOKENS   # 1_002_300
NEW_REQUEST_TOKEN_LIMIT = MAX_INPUT_TOKENS + NEW_MAX_OUTPUT_TOKENS   # 1_004_600
OLD_DAILY_TOKEN_LIMIT = OLD_REQUEST_TOKEN_LIMIT * 10                 # 10_023_000
NEW_DAILY_TOKEN_LIMIT = NEW_REQUEST_TOKEN_LIMIT * 10                 # 10_046_000
OLD_MONTHLY_TOKEN_LIMIT = OLD_DAILY_TOKEN_LIMIT * 5                  # 50_115_000
NEW_MONTHLY_TOKEN_LIMIT = NEW_DAILY_TOKEN_LIMIT * 5                  # 50_230_000

MAX_COST_USD = {
    "deepseek-v4-flash": "0.23000000",
    "deepseek-v4-pro": "0.67000000",
}

BUDGET_POLICY_PROVIDER = "deepseek"
BUDGET_POLICY_MODEL = "deepseek-v4-flash"
BUDGET_POLICY_MODULE = "shadow_portfolio"


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
        raise RuntimeError("ACTIVE_DEEPSEEK_ANALYSIS_PROFILES_INCOMPLETE")

    for row in rows:
        row = dict(row)
        expected_current = OLD_MAX_OUTPUT_TOKENS if not reverse else NEW_MAX_OUTPUT_TOKENS
        if int(row["max_output_tokens"]) != expected_current:
            raise RuntimeError(f"UNEXPECTED_CURRENT_MAX_OUTPUT_TOKENS:{row['slug']}: {row['max_output_tokens']}")

        target_max_output = OLD_MAX_OUTPUT_TOKENS if reverse else NEW_MAX_OUTPUT_TOKENS
        target_request_limit = OLD_REQUEST_TOKEN_LIMIT if reverse else NEW_REQUEST_TOKEN_LIMIT
        target_daily = OLD_DAILY_TOKEN_LIMIT if reverse else NEW_DAILY_TOKEN_LIMIT
        target_monthly = OLD_MONTHLY_TOKEN_LIMIT if reverse else NEW_MONTHLY_TOKEN_LIMIT
        target_cost = MAX_COST_USD[row["model"]]

        worst_case = (
            MAX_INPUT_TOKENS * Decimal(row["input_cost_per_million"])
            + target_max_output * Decimal(row["output_cost_per_million"])
        ) / Decimal("1000000")
        if worst_case > Decimal(target_cost):
            raise RuntimeError(f"DEEPSEEK_PROFILE_COST_CAP_BELOW_WORST_CASE:{row['slug']}")

        profile_version = int(row["profile_version"]) + (1 if not reverse else -1)
        if profile_version < 1:
            raise RuntimeError("DEEPSEEK_PROFILE_VERSION_INVALID")

        row["max_cost_usd"] = target_cost
        row["max_output_tokens"] = target_max_output
        row["request_token_limit"] = target_request_limit
        row["daily_token_limit"] = target_daily
        row["monthly_token_limit"] = target_monthly
        row["profile_version"] = profile_version
        snapshot = _snapshot(row)
        bind.execute(sa.text("""
            UPDATE ai_analysis_profiles
               SET max_cost_usd = :max_cost_usd,
                   max_output_tokens = :max_output_tokens,
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
            "max_output_tokens": target_max_output,
            "request_token_limit": target_request_limit,
            "daily_token_limit": target_daily,
            "monthly_token_limit": target_monthly,
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
        "request_token_limit": OLD_REQUEST_TOKEN_LIMIT if reverse else NEW_REQUEST_TOKEN_LIMIT,
        "daily_token_limit": OLD_DAILY_TOKEN_LIMIT if reverse else NEW_DAILY_TOKEN_LIMIT,
        "monthly_token_limit": OLD_MONTHLY_TOKEN_LIMIT if reverse else NEW_MONTHLY_TOKEN_LIMIT,
    })


def upgrade() -> None:
    _apply_profiles(reverse=False)
    _apply_budget_policy(reverse=False)


def downgrade() -> None:
    _apply_profiles(reverse=True)
    _apply_budget_policy(reverse=True)
