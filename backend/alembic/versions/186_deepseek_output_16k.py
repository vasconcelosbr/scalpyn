"""Raise DeepSeek root-cause profiles' max_output_tokens 4600 -> 16000.

Revision ID: 186_deepseek_output_16k
Revises: 185_deepseek_output_ceiling

Two consecutive real DeepSeek root-cause-audit runs (2026-08-18 22:46 UTC and
2026-08-19 11:21 UTC) both hit PROVIDER_OUTPUT_TRUNCATED with
tokens_output landing EXACTLY at the cap in force each time (2300, then
4600 after 185) -- no sign of convergence as the budget doubled. By
contrast every successful Anthropic completion sampled from ai_usage_records
(same-scale or larger inputs, up to 436819 tokens) used at most 3465 output
tokens. This is not "needs slightly more room" -- something in DeepSeek's
completion is consuming output budget at a scale Anthropic never approaches
for the same task (root cause not confirmed; plausibly reasoning/thinking
content counted against the same token budget as the final JSON answer,
consistent with DeepSeek's public reasoning-model lineage, but this is not
verified against DeepSeek's own docs).

Per the account owner's explicit choice: one larger jump (16000, ~3.5x the
previous 4600) rather than another small doubling, scoped ONLY to the
root-cause-audit-v2 profiles (root-cause-deepseek-v4-flash/pro) since
that's the only profile actually exercised at this budget so far --
systemic-overview and risk-anomalies stay at 4600 (185's value) until
they're tested too.

max_cost_usd: flash's existing $0.23 cap already covers the new worst case
(1,000,000*0.22 + 16,000*0.22)/1e6 = $0.22352). pro's existing $0.67 cap
does NOT ((1,000,000*0.66 + 16,000*0.66)/1e6 = $0.67056 > $0.67) -- raised
to $0.68. upgrade() asserts the worst-case check and aborts if a future
edit invalidates it, same guard as 179/185.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "186_deepseek_output_16k"
down_revision = "185_deepseek_output_ceiling"
branch_labels = None
depends_on = None

PROFILE_SLUGS = ("root-cause-deepseek-v4-flash", "root-cause-deepseek-v4-pro")

MAX_INPUT_TOKENS = 1_000_000
OLD_MAX_OUTPUT_TOKENS = 4_600
NEW_MAX_OUTPUT_TOKENS = 16_000

OLD_REQUEST_TOKEN_LIMIT = MAX_INPUT_TOKENS + OLD_MAX_OUTPUT_TOKENS   # 1_004_600
NEW_REQUEST_TOKEN_LIMIT = MAX_INPUT_TOKENS + NEW_MAX_OUTPUT_TOKENS   # 1_016_000
OLD_DAILY_TOKEN_LIMIT = OLD_REQUEST_TOKEN_LIMIT * 10                 # 10_046_000
NEW_DAILY_TOKEN_LIMIT = NEW_REQUEST_TOKEN_LIMIT * 10                 # 10_160_000
OLD_MONTHLY_TOKEN_LIMIT = OLD_DAILY_TOKEN_LIMIT * 5                  # 50_230_000
NEW_MONTHLY_TOKEN_LIMIT = NEW_DAILY_TOKEN_LIMIT * 5                  # 50_800_000

OLD_MAX_COST_USD = {
    "deepseek-v4-flash": "0.23000000",
    "deepseek-v4-pro": "0.67000000",
}
NEW_MAX_COST_USD = {
    "deepseek-v4-flash": "0.23000000",
    "deepseek-v4-pro": "0.68000000",
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
        raise RuntimeError("ACTIVE_DEEPSEEK_ROOT_CAUSE_PROFILES_INCOMPLETE")

    for row in rows:
        row = dict(row)
        expected_current = OLD_MAX_OUTPUT_TOKENS if not reverse else NEW_MAX_OUTPUT_TOKENS
        if int(row["max_output_tokens"]) != expected_current:
            raise RuntimeError(f"UNEXPECTED_CURRENT_MAX_OUTPUT_TOKENS:{row['slug']}: {row['max_output_tokens']}")

        target_max_output = OLD_MAX_OUTPUT_TOKENS if reverse else NEW_MAX_OUTPUT_TOKENS
        target_request_limit = OLD_REQUEST_TOKEN_LIMIT if reverse else NEW_REQUEST_TOKEN_LIMIT
        target_daily = OLD_DAILY_TOKEN_LIMIT if reverse else NEW_DAILY_TOKEN_LIMIT
        target_monthly = OLD_MONTHLY_TOKEN_LIMIT if reverse else NEW_MONTHLY_TOKEN_LIMIT
        target_cost = OLD_MAX_COST_USD[row["model"]] if reverse else NEW_MAX_COST_USD[row["model"]]

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
