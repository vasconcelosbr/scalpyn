"""Raise daily/monthly budget on ai_analysis_profiles itself, the real source of truth.

Revision ID: 182_profile_daily_budget
Revises: 181_shadow_daily_budget

181 raised ai_budget_policies.daily_token_limit / monthly_token_limit directly,
but that table is a write-through cache: every /analysis-runs/from-profile
call runs _persist_model_approval() (ai_modules.py), which unconditionally
overwrites the matching ai_budget_policies row's daily/monthly limits from
the analysis profile's own daily_token_limit / monthly_token_limit columns --
which migration 179 had left at exactly 1x / 5x request_token_limit. 181's
direct edit survived only until the very next request against that
(provider, model, module) triple, at which point it silently reverted and
AI_DAILY_TOKEN_BUDGET_EXCEEDED fired again for the same 434-trade sample.

This migration raises the real source -- ai_analysis_profiles -- for all 9
profiles (3 models x {root-cause, systemic-overview, risk-anomalies}), all of
which share module=shadow_portfolio and therefore feed the same 3
(provider, model, shadow_portfolio) ai_budget_policies rows. daily = 10x
request_token_limit, monthly = 5x that (same ratio 179 already used
elsewhere). request_token_limit / max_input_tokens / max_cost_usd are
untouched -- only the daily/monthly ceiling changes.
"""

from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "182_profile_daily_budget"
down_revision = "181_shadow_daily_budget"
branch_labels = None
depends_on = None

PROFILE_SLUGS = (
    "systemic-overview", "systemic-overview-sonnet-5", "systemic-overview-opus-5",
    "root-cause", "root-cause-sonnet-5", "root-cause-opus-5",
    "risk-anomalies", "risk-anomalies-sonnet-5", "risk-anomalies-opus-5",
)
DAILY_MULTIPLIER = 10
MONTHLY_MULTIPLIER = 5  # of the new daily value


def _canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _snapshot(row: dict) -> dict:
    return {
        "slug": row["slug"], "name": row["name"], "description": row["description"],
        "provider": row["provider"], "model": row["model"], "analysis_mode": row["analysis_mode"],
        "authority": row["authority"], "question_template": row["question_template"],
        "max_cost_usd": row["max_cost_usd"],
        "input_cost_per_million": row["input_cost_per_million"],
        "output_cost_per_million": row["output_cost_per_million"],
        "max_input_tokens": row["max_input_tokens"], "max_output_tokens": row["max_output_tokens"],
        "request_token_limit": row["request_token_limit"],
        "daily_token_limit": row["daily_token_limit"], "monthly_token_limit": row["monthly_token_limit"],
        "pricing_source_url": row["pricing_source_url"],
        "pricing_observed_at": row["pricing_observed_at"].isoformat(),
        "pricing_valid_until": row["pricing_valid_until"].isoformat(),
        "profile_version": row["profile_version"],
    }


def _apply(*, reverse: bool) -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("""
        SELECT slug, name, description, provider, model, analysis_mode, authority,
               question_template, max_cost_usd, input_cost_per_million,
               output_cost_per_million, max_input_tokens, max_output_tokens,
               request_token_limit, daily_token_limit, monthly_token_limit,
               pricing_source_url, pricing_observed_at, pricing_valid_until,
               profile_version, profile_hash
          FROM ai_analysis_profiles
         WHERE slug = ANY(:slugs) AND is_active IS TRUE
    """), {"slugs": list(PROFILE_SLUGS)}).mappings().all()
    if len(rows) != len(PROFILE_SLUGS):
        raise RuntimeError(f"ACTIVE_ANALYSIS_PROFILES_INCOMPLETE: got {len(rows)}")

    for row in rows:
        current = dict(row)
        current_hash = _canonical_hash(_snapshot(current))
        if current_hash != row["profile_hash"]:
            raise RuntimeError(f"HASH_FUNCTION_MISMATCH_ON_CURRENT_ROW:{row['slug']}")

        request_limit = int(row["request_token_limit"])
        old_daily, old_monthly = request_limit, request_limit * 5
        new_daily, new_monthly = request_limit * DAILY_MULTIPLIER, request_limit * DAILY_MULTIPLIER * MONTHLY_MULTIPLIER

        if reverse:
            if int(row["daily_token_limit"]) != new_daily or int(row["monthly_token_limit"]) != new_monthly:
                raise RuntimeError(f"UNEXPECTED_CURRENT_BUDGET_ON_DOWNGRADE:{row['slug']}")
            target_daily, target_monthly = old_daily, old_monthly
        else:
            if int(row["daily_token_limit"]) != old_daily or int(row["monthly_token_limit"]) != old_monthly:
                raise RuntimeError(f"UNEXPECTED_CURRENT_BUDGET_ON_UPGRADE:{row['slug']}")
            target_daily, target_monthly = new_daily, new_monthly

        new_version = int(row["profile_version"]) + 1
        new_row = {**current, "daily_token_limit": target_daily, "monthly_token_limit": target_monthly,
                   "profile_version": new_version}
        new_hash = _canonical_hash(_snapshot(new_row))

        bind.execute(sa.text("""
            UPDATE ai_analysis_profiles
               SET daily_token_limit=:daily, monthly_token_limit=:monthly,
                   profile_version=:version, profile_hash=:hash, updated_at=NOW()
             WHERE slug=:slug AND is_active IS TRUE
        """), {"daily": target_daily, "monthly": target_monthly, "version": new_version,
               "hash": new_hash, "slug": row["slug"]})


def upgrade() -> None:
    _apply(reverse=False)


def downgrade() -> None:
    _apply(reverse=True)
