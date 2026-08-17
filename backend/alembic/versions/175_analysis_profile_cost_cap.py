"""Raise per-profile cost caps to match the real live token ceiling.

Revision ID: 175_analysis_profile_cost_cap
Revises: 174_chat_response_language

Every ai_analysis_profiles row's max_cost_usd was sized against
max_input_tokens=200000 (a soft, declarative field used only for a catalog
sanity check at approval-creation time). The actual runtime gate in
systemic_langgraph_bridge.py computes
``max_input_tokens = budget.request_token_limit - approval.max_output_tokens``
using AIBudgetPolicyRecord.request_token_limit, which is 444600 (unchanged,
never derived from the profile's own field) -- 442300 allowed input tokens,
not 200000. A request large enough to approach that real ceiling (confirmed
live 2026-08-16: a Shadow Portfolio "Causa raiz" run over 400 trades on
claude-opus-5) passes every token-count gate but still gets rejected by
MODEL_COST_APPROVAL_LIMIT_EXCEEDED_BEFORE_CALL, because the dollar cap was
never sized for more than 200000 input tokens' worth of spend. This was
already marginal for Haiku (worst case at 442300 tokens is $0.4538, already
above the $0.45 cap) and is far worse for Sonnet 5 / Opus 5's higher per-
token pricing. Recomputes max_cost_usd at the real 442300-token ceiling for
all 9 profiles so the cost gate never blocks a request the token-budget gate
would otherwise allow.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from alembic import op
import sqlalchemy as sa


revision = "175_analysis_profile_cost_cap"
down_revision = "174_chat_response_language"
branch_labels = None
depends_on = None

PROFILE_SLUGS = (
    "systemic-overview", "systemic-overview-sonnet-5", "systemic-overview-opus-5",
    "root-cause", "root-cause-sonnet-5", "root-cause-opus-5",
    "risk-anomalies", "risk-anomalies-sonnet-5", "risk-anomalies-opus-5",
)

# The real enforcement ceiling: AIBudgetPolicyRecord.request_token_limit
# (444600, unrelated to and never derived from this table's own
# max_input_tokens) minus the profile's max_output_tokens (2300).
REAL_MAX_INPUT_TOKENS = 444_600 - 2_300

# Rounded up from the exact worst case at REAL_MAX_INPUT_TOKENS tokens for
# each model's real per-token price, leaving deliberate headroom instead of
# landing pennies above the exact figure again.
NEW_MAX_COST_USD = {
    "claude-haiku-4-5-20251001": "0.50000000",
    "claude-sonnet-5": "1.00000000",
    "claude-opus-5": "2.50000000",
}

# The value every row actually had before this migration (155/173) --
# downgrade restores these exactly, it does not try to derive them back.
OLD_MAX_COST_USD = {
    "claude-haiku-4-5-20251001": "0.45000000",
    "claude-sonnet-5": "0.45000000",
    "claude-opus-5": "1.10000000",
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


def _apply(*, reverse: bool) -> None:
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
        input_tokens_for_check = int(row["max_input_tokens"]) if reverse else REAL_MAX_INPUT_TOKENS
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
        row["profile_version"] = profile_version
        snapshot = _snapshot(row)
        bind.execute(sa.text("""
            UPDATE ai_analysis_profiles
               SET max_cost_usd = :max_cost_usd,
                   profile_version = :profile_version,
                   profile_hash = :profile_hash,
                   updated_at = NOW()
             WHERE slug = :slug AND is_active IS TRUE
        """), {
            "slug": row["slug"],
            "max_cost_usd": target_cost,
            "profile_version": profile_version,
            "profile_hash": _canonical_hash(snapshot),
        })


def upgrade() -> None:
    _apply(reverse=False)


def downgrade() -> None:
    _apply(reverse=True)
