"""Use DeepSeek V4's full provider output capacity for every analysis profile.

Revision ID: 187_deepseek_provider_max
Revises: 186_deepseek_output_16k

The production systemic-overview run on 2026-08-20 reached exactly 4,600
output tokens and stopped with ``finish_reason=length``. Revision 186 would
only raise the two root-cause profiles to 16,000, leaving systemic-overview
and risk-anomalies capped at 4,600.

DeepSeek's official V4 documentation, checked 2026-08-20, declares a 1M
context and a 384K maximum output for both V4 Flash and V4 Pro. This revision
removes Scalpyn's lower output ceiling by applying that physical provider
maximum to all six DeepSeek analysis profiles. Aggregate daily/monthly token
quotas are raised to PostgreSQL INTEGER's maximum so they cannot interrupt
normal analysis execution. Per-request values still represent the provider's
physical envelope and usage remains metered and auditable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "187_deepseek_provider_max"
down_revision = "186_deepseek_output_16k"
branch_labels = None
depends_on = None

PROFILE_SLUGS = (
    "systemic-overview-deepseek-v4-flash", "systemic-overview-deepseek-v4-pro",
    "root-cause-deepseek-v4-flash", "root-cause-deepseek-v4-pro",
    "risk-anomalies-deepseek-v4-flash", "risk-anomalies-deepseek-v4-pro",
)
ROOT_CAUSE_SLUGS = frozenset({
    "root-cause-deepseek-v4-flash", "root-cause-deepseek-v4-pro",
})

MAX_INPUT_TOKENS = 1_000_000
NEW_MAX_OUTPUT_TOKENS = 384_000
NEW_REQUEST_TOKEN_LIMIT = MAX_INPUT_TOKENS + NEW_MAX_OUTPUT_TOKENS
NEW_DAILY_TOKEN_LIMIT = 2_147_483_647
NEW_MONTHLY_TOKEN_LIMIT = 2_147_483_647

OLD_PRICING = {
    "deepseek-v4-flash": ("0.22000000", "0.22000000"),
    "deepseek-v4-pro": ("0.66000000", "0.66000000"),
}
NEW_PRICING = {
    "deepseek-v4-flash": ("0.14000000", "0.28000000"),
    "deepseek-v4-pro": ("0.43500000", "0.87000000"),
}
NEW_MAX_COST_USD = {
    "deepseek-v4-flash": "0.25000000",
    "deepseek-v4-pro": "0.77000000",
}
OLD_MAX_COST_USD = {
    "deepseek-v4-flash": "0.23000000",
    "deepseek-v4-pro": "0.67000000",
}
OLD_ROOT_CAUSE_PRO_MAX_COST_USD = "0.68000000"

PRICING_SOURCE_URL = "https://api-docs.deepseek.com/quick_start/pricing/"
NEW_PRICING_OBSERVED_AT = datetime(2026, 8, 20, tzinfo=timezone.utc)
NEW_PRICING_VALID_UNTIL = datetime(2026, 11, 18, tzinfo=timezone.utc)
OLD_PRICING_SOURCE_URL = "https://api-docs.deepseek.com/"
OLD_PRICING_OBSERVED_AT = datetime(2026, 8, 18, tzinfo=timezone.utc)
OLD_PRICING_VALID_UNTIL = datetime(2026, 11, 16, tzinfo=timezone.utc)

BUDGET_POLICY_PROVIDER = "deepseek"
BUDGET_POLICY_MODEL = "deepseek-v4-flash"
BUDGET_POLICY_MODULE = "shadow_portfolio"


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot(row: dict) -> dict:
    return {
        "slug": row["slug"], "name": row["name"],
        "description": row["description"], "provider": row["provider"],
        "model": row["model"], "analysis_mode": row["analysis_mode"],
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


def _old_values(row: dict) -> dict:
    root_cause = row["slug"] in ROOT_CAUSE_SLUGS
    old_output = 16_000 if root_cause else 4_600
    old_request = MAX_INPUT_TOKENS + old_output
    old_cost = OLD_MAX_COST_USD[row["model"]]
    if root_cause and row["model"] == "deepseek-v4-pro":
        old_cost = OLD_ROOT_CAUSE_PRO_MAX_COST_USD
    return {
        "max_output_tokens": old_output,
        "request_token_limit": old_request,
        "daily_token_limit": old_request * 10,
        "monthly_token_limit": old_request * 50,
        "max_cost_usd": old_cost,
        "input_cost_per_million": OLD_PRICING[row["model"]][0],
        "output_cost_per_million": OLD_PRICING[row["model"]][1],
        "pricing_source_url": OLD_PRICING_SOURCE_URL,
        "pricing_observed_at": OLD_PRICING_OBSERVED_AT,
        "pricing_valid_until": OLD_PRICING_VALID_UNTIL,
    }


def _new_values(row: dict) -> dict:
    input_rate, output_rate = NEW_PRICING[row["model"]]
    worst_case = (
        MAX_INPUT_TOKENS * Decimal(input_rate)
        + NEW_MAX_OUTPUT_TOKENS * Decimal(output_rate)
    ) / Decimal("1000000")
    max_cost = NEW_MAX_COST_USD[row["model"]]
    if worst_case > Decimal(max_cost):
        raise RuntimeError(
            f"DEEPSEEK_PROVIDER_MAX_COST_CAP_BELOW_WORST_CASE:{row['slug']}"
        )
    return {
        "max_output_tokens": NEW_MAX_OUTPUT_TOKENS,
        "request_token_limit": NEW_REQUEST_TOKEN_LIMIT,
        "daily_token_limit": NEW_DAILY_TOKEN_LIMIT,
        "monthly_token_limit": NEW_MONTHLY_TOKEN_LIMIT,
        "max_cost_usd": max_cost,
        "input_cost_per_million": input_rate,
        "output_cost_per_million": output_rate,
        "pricing_source_url": PRICING_SOURCE_URL,
        "pricing_observed_at": NEW_PRICING_OBSERVED_AT,
        "pricing_valid_until": NEW_PRICING_VALID_UNTIL,
    }


def _apply_profiles(*, reverse: bool) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT slug, name, description, provider, model, analysis_mode,
               authority, question_template, max_cost_usd,
               input_cost_per_million, output_cost_per_million,
               max_input_tokens, max_output_tokens, request_token_limit,
               daily_token_limit, monthly_token_limit, pricing_source_url,
               pricing_observed_at, pricing_valid_until, profile_version
          FROM ai_analysis_profiles
         WHERE slug IN :slugs AND is_active IS TRUE
         ORDER BY slug
    """).bindparams(sa.bindparam("slugs", expanding=True)), {
        "slugs": list(PROFILE_SLUGS),
    }).mappings().all()
    if len(rows) != len(PROFILE_SLUGS):
        raise RuntimeError("ACTIVE_DEEPSEEK_ANALYSIS_PROFILES_INCOMPLETE")

    for selected in rows:
        row = dict(selected)
        old_values = _old_values(row)
        expected_output = (
            NEW_MAX_OUTPUT_TOKENS if reverse
            else old_values["max_output_tokens"]
        )
        if int(row["max_output_tokens"]) != expected_output:
            raise RuntimeError(
                f"UNEXPECTED_CURRENT_MAX_OUTPUT_TOKENS:{row['slug']}: "
                f"{row['max_output_tokens']}"
            )

        target = old_values if reverse else _new_values(row)
        row.update(target)
        row["profile_version"] = int(row["profile_version"]) + (-1 if reverse else 1)
        if row["profile_version"] < 1:
            raise RuntimeError("DEEPSEEK_PROFILE_VERSION_INVALID")
        profile_hash = _canonical_hash(_snapshot(row))

        bind.execute(sa.text("""
            UPDATE ai_analysis_profiles
               SET max_cost_usd = :max_cost_usd,
                   input_cost_per_million = :input_cost_per_million,
                   output_cost_per_million = :output_cost_per_million,
                   max_output_tokens = :max_output_tokens,
                   request_token_limit = :request_token_limit,
                   daily_token_limit = :daily_token_limit,
                   monthly_token_limit = :monthly_token_limit,
                   pricing_source_url = :pricing_source_url,
                   pricing_observed_at = :pricing_observed_at,
                   pricing_valid_until = :pricing_valid_until,
                   profile_version = :profile_version,
                   profile_hash = :profile_hash,
                   updated_at = NOW()
             WHERE slug = :slug AND is_active IS TRUE
        """), {**target, "slug": row["slug"],
                 "profile_version": row["profile_version"],
                 "profile_hash": profile_hash})


def _apply_budget_policy(*, reverse: bool) -> None:
    bind = op.get_bind()
    old_request = MAX_INPUT_TOKENS + 16_000
    bind.execute(sa.text("""
        UPDATE ai_budget_policies
           SET request_token_limit = :request_token_limit,
               daily_token_limit = :daily_token_limit,
               monthly_token_limit = :monthly_token_limit
         WHERE provider = :provider AND model = :model
           AND module = :module AND is_active IS TRUE
    """), {
        "provider": BUDGET_POLICY_PROVIDER,
        "model": BUDGET_POLICY_MODEL,
        "module": BUDGET_POLICY_MODULE,
        "request_token_limit": old_request if reverse else NEW_REQUEST_TOKEN_LIMIT,
        "daily_token_limit": old_request * 10 if reverse else NEW_DAILY_TOKEN_LIMIT,
        "monthly_token_limit": old_request * 50 if reverse else NEW_MONTHLY_TOKEN_LIMIT,
    })


def upgrade() -> None:
    _apply_profiles(reverse=False)
    _apply_budget_policy(reverse=False)


def downgrade() -> None:
    _apply_profiles(reverse=True)
    _apply_budget_policy(reverse=True)
