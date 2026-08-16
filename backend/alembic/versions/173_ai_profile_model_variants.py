"""Add Claude Sonnet 5 and Claude Opus 5 variants of every governed analysis profile.

Revision ID: 173_ai_profile_model_variants
Revises: 172_chat_identity_object

Each existing profile (systemic-overview, root-cause, risk-anomalies) is
Haiku-only today. This adds one Sonnet 5 and one Opus 5 sibling per profile,
sharing the same name/description/question_template so the frontend can group
them as "analysis type" cards with a model sub-selector. Pricing is Anthropic's
published per-token rate (https://platform.claude.com/docs/en/about-claude/pricing,
observed 2026-08-16) applied to the same token budget as the live Haiku rows
(max_input_tokens=200000, max_output_tokens=2300), each row is analysis-only.
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


revision = "173_ai_profile_model_variants"
down_revision = "172_chat_identity_object"
branch_labels = None
depends_on = None

PRICING_SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_OBSERVED_AT = datetime(2026, 8, 16, tzinfo=timezone.utc)
PRICING_VALID_UNTIL = datetime(2026, 11, 14, tzinfo=timezone.utc)

# Token budget mirrors the live Haiku rows seeded by 155/160 as of 2026-08-16.
MAX_INPUT_TOKENS = 200_000
MAX_OUTPUT_TOKENS = 2_300
REQUEST_TOKEN_LIMIT = 444_600
DAILY_TOKEN_LIMIT = 889_200
MONTHLY_TOKEN_LIMIT = 4_446_000

# (model_id, input_cost_per_million, output_cost_per_million, max_cost_usd)
# max_cost_usd is rounded up from the worst-case cost at the token budget above.
MODEL_VARIANTS = (
    ("sonnet-5", "claude-sonnet-5", "2.00000000", "10.00000000", "0.45000000"),
    ("opus-5", "claude-opus-5", "5.00000000", "25.00000000", "1.10000000"),
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
        "provider": "anthropic",
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
                display_order=base["display_order_base"] + offset,
            ))
    return profiles


PROFILES = tuple(_build_profiles())


def upgrade() -> None:
    table = sa.table(
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
    op.bulk_insert(table, list(PROFILES))


def downgrade() -> None:
    slugs = [row["slug"] for row in PROFILES]
    op.execute(
        sa.text("DELETE FROM ai_analysis_profiles WHERE slug = ANY(:slugs)").bindparams(
            sa.bindparam("slugs", expanding=True)
        ),
        {"slugs": slugs},
    )
