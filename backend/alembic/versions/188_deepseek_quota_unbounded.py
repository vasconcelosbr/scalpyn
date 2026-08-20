"""Remove residual internal DeepSeek quotas from Intelligence Runs.

Revision ID: 188_deepseek_quota_unbounded
Revises: 187_deepseek_provider_max

Revision 187 moved every DeepSeek analysis profile to V4's physical 384K
output maximum, but a production read-back found two independent lower
quota layers still present:

* the existing V4 Pro ``shadow_portfolio`` budget policy remained at the old
  1,004,600 request / 10,046,000 daily / 50,230,000 monthly values;
* the active DeepSeek key retained a 100,000,000 monthly-token ceiling.

The account owner explicitly requested that Intelligence Runs execute without
internal token limits or quota stops. PostgreSQL requires concrete values in
parts of this contract, so this revision uses the maximum representable value
for each column while keeping metering, reconciliation, ANALYSIS_ONLY and
fail-closed output validation intact.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "188_deepseek_quota_unbounded"
down_revision = "187_deepseek_provider_max"
branch_labels = None
depends_on = None

PROVIDER = "deepseek"
MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
MODULE = "shadow_portfolio"

REQUEST_TOKEN_LIMIT = 1_384_000
POLICY_AGGREGATE_TOKEN_LIMIT = 2_147_483_647
KEY_MONTHLY_TOKEN_LIMIT = 9_223_372_036_854_775_807
OLD_KEY_MONTHLY_TOKEN_LIMIT = 100_000_000

OLD_POLICY_VALUES = {
    "deepseek-v4-flash": (1_384_000, 2_147_483_647, 2_147_483_647),
    "deepseek-v4-pro": (1_004_600, 10_046_000, 50_230_000),
}


def _active_policy_rows(bind) -> list[dict]:
    return [dict(row) for row in bind.execute(sa.text("""
        SELECT model, request_token_limit, daily_token_limit,
               monthly_token_limit
          FROM ai_budget_policies
         WHERE provider = :provider AND model IN :models
           AND module = :module AND is_active IS TRUE
         ORDER BY model
    """).bindparams(sa.bindparam("models", expanding=True)), {
        "provider": PROVIDER,
        "models": list(MODELS),
        "module": MODULE,
    }).mappings().all()]


def _assert_expected_policies(rows: list[dict], *, reverse: bool) -> None:
    if len(rows) != len(MODELS):
        raise RuntimeError("ACTIVE_DEEPSEEK_SHADOW_BUDGET_POLICIES_INCOMPLETE")
    for row in rows:
        expected = (
            (REQUEST_TOKEN_LIMIT, POLICY_AGGREGATE_TOKEN_LIMIT,
             POLICY_AGGREGATE_TOKEN_LIMIT)
            if reverse else OLD_POLICY_VALUES[row["model"]]
        )
        current = (
            int(row["request_token_limit"]), int(row["daily_token_limit"]),
            int(row["monthly_token_limit"]),
        )
        if current != expected:
            raise RuntimeError(
                f"UNEXPECTED_DEEPSEEK_SHADOW_BUDGET:{row['model']}:{current}"
            )


def _apply_policies(bind, *, reverse: bool) -> None:
    rows = _active_policy_rows(bind)
    _assert_expected_policies(rows, reverse=reverse)
    for row in rows:
        target = (
            OLD_POLICY_VALUES[row["model"]]
            if reverse else (
                REQUEST_TOKEN_LIMIT, POLICY_AGGREGATE_TOKEN_LIMIT,
                POLICY_AGGREGATE_TOKEN_LIMIT,
            )
        )
        bind.execute(sa.text("""
            UPDATE ai_budget_policies
               SET request_token_limit = :request_limit,
                   daily_token_limit = :daily_limit,
                   monthly_token_limit = :monthly_limit
             WHERE provider = :provider AND model = :model
               AND module = :module AND is_active IS TRUE
        """), {
            "provider": PROVIDER, "model": row["model"], "module": MODULE,
            "request_limit": target[0], "daily_limit": target[1],
            "monthly_limit": target[2],
        })


def _apply_active_keys(bind, *, reverse: bool) -> None:
    rows = bind.execute(sa.text("""
        SELECT id, monthly_token_limit
          FROM ai_provider_keys
         WHERE provider = :provider AND is_active IS TRUE
         ORDER BY id
    """), {"provider": PROVIDER}).mappings().all()
    if not rows:
        raise RuntimeError("ACTIVE_DEEPSEEK_PROVIDER_KEY_MISSING")
    expected = KEY_MONTHLY_TOKEN_LIMIT if reverse else OLD_KEY_MONTHLY_TOKEN_LIMIT
    for row in rows:
        if int(row["monthly_token_limit"] or 0) != expected:
            raise RuntimeError(
                f"UNEXPECTED_DEEPSEEK_KEY_MONTHLY_LIMIT:{row['id']}:"
                f"{row['monthly_token_limit']}"
            )
    bind.execute(sa.text("""
        UPDATE ai_provider_keys
           SET monthly_token_limit = :monthly_limit,
               updated_at = NOW()
         WHERE provider = :provider AND is_active IS TRUE
    """), {
        "provider": PROVIDER,
        "monthly_limit": (
            OLD_KEY_MONTHLY_TOKEN_LIMIT if reverse
            else KEY_MONTHLY_TOKEN_LIMIT
        ),
    })


def upgrade() -> None:
    bind = op.get_bind()
    _apply_policies(bind, reverse=False)
    _apply_active_keys(bind, reverse=False)


def downgrade() -> None:
    bind = op.get_bind()
    _apply_active_keys(bind, reverse=True)
    _apply_policies(bind, reverse=True)
