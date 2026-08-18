"""Add DeepSeek (v4-flash / v4-pro) variants of every governed analysis profile.

Revision ID: 184_deepseek_analysis_profiles
Revises: 183_ai_key_default_model

Mirrors 173_ai_profile_model_variants.py's approach: one new row per
(base profile x model) pair, sharing name/description/question_template with
the Anthropic rows so the frontend groups them into the same cards.

Token budget matches the live Sonnet-5/Opus-5 rows as of 2026-08-18
(max_input_tokens=1_000_000, max_output_tokens=2_300 -- confirmed by querying
production ai_analysis_profiles directly, not copied from an older migration
snapshot, since 173's original 200_000 baseline was later raised by 179).
request/daily/monthly token limits reuse the exact live Sonnet-5/Opus-5
values for that same budget (1_002_300 / 10_023_000 / 50_115_000).

Pricing: DeepSeek's blended per-token rate as given directly by the account
owner (2026-08-18): deepseek-v4-flash $0.22/M, deepseek-v4-pro $0.66/M,
applied to both input_cost_per_million and output_cost_per_million (no
separate input/output split was provided). max_cost_usd is the worst-case
cost at the token budget above, rounded up to the next cent -- NOT a
fabricated number, see the worst-case formula enforced at
app/api/ai_modules.py (MODEL_COST_CAP_BELOW_WORST_CASE):
  flash: (1_000_000*0.22 + 2_300*0.22) / 1e6 = 0.220506 -> cap 0.23
  pro:   (1_000_000*0.66 + 2_300*0.66) / 1e6 = 0.661518 -> cap 0.67

Also widens ck_ai_analysis_profile_provider (originally
anthropic/openai/gemini only, 155_ai_analysis_profiles.py) to allow
'deepseek', or the INSERT below violates the live CHECK constraint.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "184_deepseek_analysis_profiles"
down_revision = "183_ai_key_default_model"
branch_labels = None
depends_on = None

_CHECK_NAME = "ck_ai_analysis_profile_provider"
_OLD_PROVIDERS = ("anthropic", "openai", "gemini")
_NEW_PROVIDERS = ("anthropic", "openai", "gemini", "deepseek")

PRICING_SOURCE_URL = "https://api-docs.deepseek.com/"
PRICING_OBSERVED_AT = datetime(2026, 8, 18, tzinfo=timezone.utc)
PRICING_VALID_UNTIL = datetime(2026, 11, 16, tzinfo=timezone.utc)

# Token budget mirrors the live Sonnet-5/Opus-5 rows as of 2026-08-18.
MAX_INPUT_TOKENS = 1_000_000
MAX_OUTPUT_TOKENS = 2_300
REQUEST_TOKEN_LIMIT = 1_002_300
DAILY_TOKEN_LIMIT = 10_023_000
MONTHLY_TOKEN_LIMIT = 50_115_000

# (model_suffix, model_id, input_cost_per_million, output_cost_per_million, max_cost_usd)
MODEL_VARIANTS = (
    ("deepseek-v4-flash", "deepseek-v4-flash", "0.22000000", "0.22000000", "0.23000000"),
    ("deepseek-v4-pro", "deepseek-v4-pro", "0.66000000", "0.66000000", "0.67000000"),
)

BASE_PROFILES = (
    dict(
        base_slug="systemic-overview",
        name="Visão sistêmica",
        description="Panorama do módulo, evidências, riscos e recomendações read-only.",
        analysis_mode="SYSTEMIC",
        question_template=(
            "Produza uma análise sistêmica read-only do módulo de origem. "
            "Identifique achados, causas, riscos, evidências ausentes e recomendações, sem executar mudanças."
        ),
        display_order_base=10,
    ),
    dict(
        base_slug="root-cause",
        name="Causa raiz",
        description="Investiga a origem dos desvios e separa causa, sintoma e ausência de evidência.",
        analysis_mode="ROOT_CAUSE_AUDIT",
        question_template=(
            "Execute uma auditoria read-only de causa raiz no módulo de origem. "
            "Separe causas, sintomas, fatores contribuintes, lacunas de evidência e ações seguras recomendadas."
        ),
        display_order_base=20,
    ),
    dict(
        base_slug="risk-anomalies",
        name="Riscos e anomalias",
        description="Prioriza inconsistências, conflitos de política e sinais de risco operacional.",
        analysis_mode="SYSTEMIC",
        question_template=(
            "Analise o módulo de origem em modo read-only, priorizando anomalias, conflitos de configuração, "
            "riscos operacionais, qualidade dos dados e condições que exigem intervenção humana."
        ),
        display_order_base=30,
    ),
)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _profile(
    *, profile_id: str, slug: str, name: str, description: str, model: str,
    analysis_mode: str, question_template: str, input_cost_per_million: str,
    output_cost_per_million: str, max_cost_usd: str, display_order: int,
) -> dict:
    values = {
        "slug": slug,
        "name": name,
        "description": description,
        "provider": "deepseek",
        "model": model,
        "analysis_mode": analysis_mode,
        "authority": "ANALYSIS_ONLY",
        "question_template": question_template,
        "max_cost_usd": max_cost_usd,
        "input_cost_per_million": input_cost_per_million,
        "output_cost_per_million": output_cost_per_million,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "request_token_limit": REQUEST_TOKEN_LIMIT,
        "daily_token_limit": DAILY_TOKEN_LIMIT,
        "monthly_token_limit": MONTHLY_TOKEN_LIMIT,
        "pricing_source_url": PRICING_SOURCE_URL,
        "pricing_observed_at": PRICING_OBSERVED_AT.isoformat(),
        "pricing_valid_until": PRICING_VALID_UNTIL.isoformat(),
        "profile_version": 1,
    }
    return {
        "id": uuid.UUID(profile_id),
        **values,
        "max_cost_usd": Decimal(values["max_cost_usd"]),
        "input_cost_per_million": Decimal(values["input_cost_per_million"]),
        "output_cost_per_million": Decimal(values["output_cost_per_million"]),
        "pricing_observed_at": PRICING_OBSERVED_AT,
        "pricing_valid_until": PRICING_VALID_UNTIL,
        "profile_hash": _canonical_hash(values),
        "display_order": display_order,
        "is_active": True,
        "created_at": PRICING_OBSERVED_AT,
        "updated_at": PRICING_OBSERVED_AT,
    }


def _build_profiles() -> list[dict]:
    namespace = uuid.UUID("6a2a5a0a-8f2f-5a6a-9c1a-8e6d4b9a2b6a")
    profiles = []
    for base in BASE_PROFILES:
        for offset, (model_suffix, model_id, input_cost, output_cost, max_cost) in enumerate(MODEL_VARIANTS, start=1):
            slug = f"{base['base_slug']}-{model_suffix}"
            profiles.append(_profile(
                profile_id=str(uuid.uuid5(namespace, slug)),
                slug=slug,
                name=base["name"],
                description=base["description"],
                model=model_id,
                analysis_mode=base["analysis_mode"],
                question_template=base["question_template"],
                input_cost_per_million=input_cost,
                output_cost_per_million=output_cost,
                max_cost_usd=max_cost,
                display_order=base["display_order_base"] + offset + 2,
            ))
    return profiles


PROFILES = tuple(_build_profiles())

_TABLE = sa.table(
    "ai_analysis_profiles",
    sa.column("id", UUID(as_uuid=True)),
    sa.column("slug", sa.String),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("provider", sa.String),
    sa.column("model", sa.String),
    sa.column("analysis_mode", sa.String),
    sa.column("authority", sa.String),
    sa.column("question_template", sa.Text),
    sa.column("max_cost_usd", sa.Numeric),
    sa.column("input_cost_per_million", sa.Numeric),
    sa.column("output_cost_per_million", sa.Numeric),
    sa.column("max_input_tokens", sa.Integer),
    sa.column("max_output_tokens", sa.Integer),
    sa.column("request_token_limit", sa.Integer),
    sa.column("daily_token_limit", sa.Integer),
    sa.column("monthly_token_limit", sa.Integer),
    sa.column("pricing_source_url", sa.Text),
    sa.column("pricing_observed_at", sa.TIMESTAMP(timezone=True)),
    sa.column("pricing_valid_until", sa.TIMESTAMP(timezone=True)),
    sa.column("profile_version", sa.Integer),
    sa.column("profile_hash", sa.String),
    sa.column("display_order", sa.Integer),
    sa.column("is_active", sa.Boolean),
    sa.column("created_at", sa.TIMESTAMP(timezone=True)),
    sa.column("updated_at", sa.TIMESTAMP(timezone=True)),
)


def upgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "ai_analysis_profiles", type_="check")
    providers_sql = ", ".join(f"'{p}'" for p in _NEW_PROVIDERS)
    op.create_check_constraint(_CHECK_NAME, "ai_analysis_profiles", f"provider IN ({providers_sql})")
    op.bulk_insert(_TABLE, list(PROFILES))


def downgrade() -> None:
    slugs = [row["slug"] for row in PROFILES]
    op.execute(
        sa.text("DELETE FROM ai_analysis_profiles WHERE slug IN :slugs").bindparams(
            sa.bindparam("slugs", value=slugs, expanding=True)
        )
    )
    op.drop_constraint(_CHECK_NAME, "ai_analysis_profiles", type_="check")
    providers_sql = ", ".join(f"'{p}'" for p in _OLD_PROVIDERS)
    op.create_check_constraint(_CHECK_NAME, "ai_analysis_profiles", f"provider IN ({providers_sql})")
