"""Raise the shadow_portfolio daily/monthly budget ceiling; add the missing sonnet-5 row.

Revision ID: 181_shadow_daily_budget
Revises: 180_fix_prompt_version_id

ai_budget_policies.daily_token_limit for anthropic/claude-opus-5/shadow_portfolio
was set to exactly request_token_limit (1_002_300) by migration 179 -- it just
carried the per-request ceiling forward without considering that a user runs
more than one large analysis per day. In practice this allowed at most one
full-size Opus 5 shadow_portfolio request per day; a second request (e.g. a
434-trade Detailed Report sample) hit AI_DAILY_TOKEN_BUDGET_EXCEEDED even
though it was well within the model's real context window and today's actual
spend was nowhere near a problematic level.

The anthropic/claude-haiku-4-5-20251001/shadow_portfolio row has the same
1x-request daily ceiling, while other modules on the same table (e.g.
strategy_profiles: daily = 22x request) already carry a much more generous
multiplier -- shadow_portfolio was never deliberately tightened, it just never
got the same multiplier applied. Raise both rows to daily = 10x request,
monthly = 5x daily (matching the daily/monthly ratio migration 179 already
uses elsewhere).

There is also no anthropic/claude-sonnet-5/shadow_portfolio row at all --
any request that resolves to Sonnet 5 for this module would fail the budget
lookup outright (root-cause-sonnet-5 / systemic-overview-sonnet-5 /
risk-anomalies-sonnet-5 profiles all target this module). Insert it with the
same values as the opus-5 row, since both share the same real max_input_tokens
(1_000_000) and request_token_limit (1_002_300).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "181_shadow_daily_budget"
down_revision = "180_fix_prompt_version_id"
branch_labels = None
depends_on = None

DAILY_MULTIPLIER = 10
MONTHLY_MULTIPLIER = 5  # of the new daily value, matching migration 179's ratio

ROWS = (
    {"provider": "anthropic", "model": "claude-haiku-4-5-20251001", "module": "shadow_portfolio",
     "request_token_limit": 400_000},
    {"provider": "anthropic", "model": "claude-opus-5", "module": "shadow_portfolio",
     "request_token_limit": 1_002_300},
)
NEW_SONNET_ROW = {
    "provider": "anthropic", "model": "claude-sonnet-5", "module": "shadow_portfolio",
    "request_token_limit": 1_002_300,
}
TENANT_ID = "8080110c-ee9d-4a2b-a53f-6bef86dd8867"  # the sole tenant on every existing ai_budget_policies row


def _raise_existing(*, reverse: bool) -> None:
    bind = op.get_bind()
    for spec in ROWS:
        row = bind.execute(sa.text("""
            SELECT id, request_token_limit, daily_token_limit, monthly_token_limit
              FROM ai_budget_policies
             WHERE provider=:provider AND model=:model AND module=:module AND is_active IS TRUE
        """), spec).mappings().one()
        if int(row["request_token_limit"]) != spec["request_token_limit"]:
            raise RuntimeError(f"UNEXPECTED_REQUEST_TOKEN_LIMIT:{spec['model']}:{spec['module']}")
        new_daily = spec["request_token_limit"] * DAILY_MULTIPLIER
        new_monthly = new_daily * MONTHLY_MULTIPLIER
        old_daily = spec["request_token_limit"]
        old_monthly = old_daily * 5
        if reverse:
            if int(row["daily_token_limit"]) != new_daily or int(row["monthly_token_limit"]) != new_monthly:
                raise RuntimeError(f"UNEXPECTED_CURRENT_BUDGET_ON_DOWNGRADE:{spec['model']}:{spec['module']}")
            target_daily, target_monthly = old_daily, old_monthly
        else:
            if int(row["daily_token_limit"]) != old_daily or int(row["monthly_token_limit"]) != old_monthly:
                raise RuntimeError(f"UNEXPECTED_CURRENT_BUDGET_ON_UPGRADE:{spec['model']}:{spec['module']}")
            target_daily, target_monthly = new_daily, new_monthly
        bind.execute(sa.text("""
            UPDATE ai_budget_policies
               SET daily_token_limit=:daily, monthly_token_limit=:monthly
             WHERE id=:id
        """), {"id": row["id"], "daily": target_daily, "monthly": target_monthly})


def upgrade() -> None:
    _raise_existing(reverse=False)
    bind = op.get_bind()
    existing = bind.execute(sa.text("""
        SELECT id FROM ai_budget_policies
         WHERE provider=:provider AND model=:model AND module=:module
    """), NEW_SONNET_ROW).mappings().one_or_none()
    if existing is not None:
        raise RuntimeError("SONNET_SHADOW_PORTFOLIO_BUDGET_ROW_ALREADY_EXISTS")
    daily = NEW_SONNET_ROW["request_token_limit"] * DAILY_MULTIPLIER
    monthly = daily * MONTHLY_MULTIPLIER
    bind.execute(sa.text("""
        INSERT INTO ai_budget_policies (
            id, tenant_id, provider, model, module, daily_token_limit, monthly_token_limit,
            request_token_limit, null_limit_policy, is_active, created_at
        ) VALUES (
            gen_random_uuid(), :tenant_id, :provider, :model, :module, :daily, :monthly,
            :request_token_limit, 'DENY', TRUE, NOW()
        )
    """), {**NEW_SONNET_ROW, "tenant_id": TENANT_ID, "daily": daily, "monthly": monthly})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("""
        DELETE FROM ai_budget_policies
         WHERE provider=:provider AND model=:model AND module=:module
    """), NEW_SONNET_ROW)
    _raise_existing(reverse=True)
