"""Create governed predefined profiles for module analysis.

Revision ID: 155_ai_analysis_profiles
Revises: 154_structural_systemic_prompt

The profiles are analysis-only. They grant no live-write, trading, profile,
score, Spot, or ML-promotion authority.
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


revision = "155_ai_analysis_profiles"
down_revision = "154_structural_systemic_prompt"
branch_labels = None
depends_on = None

PRICING_SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_OBSERVED_AT = datetime(2026, 8, 10, tzinfo=timezone.utc)
PRICING_VALID_UNTIL = datetime(2026, 11, 8, tzinfo=timezone.utc)


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _profile(
    *, profile_id: str, slug: str, name: str, description: str,
    analysis_mode: str, question_template: str, max_output_tokens: int,
    request_token_limit: int, display_order: int,
) -> dict:
    values = {
        "slug": slug,
        "name": name,
        "description": description,
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "analysis_mode": analysis_mode,
        "authority": "ANALYSIS_ONLY",
        "question_template": question_template,
        "max_cost_usd": "0.02000000",
        "input_cost_per_million": "1.00000000",
        "output_cost_per_million": "5.00000000",
        "max_input_tokens": 12763,
        "max_output_tokens": max_output_tokens,
        "request_token_limit": request_token_limit,
        "daily_token_limit": 40000,
        "monthly_token_limit": 40000,
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


PROFILES = (
    _profile(
        profile_id="44dd0065-7de7-5b1d-bfe1-c7a5f008c9a1",
        slug="systemic-overview",
        name="Visão sistêmica",
        description="Panorama do módulo, evidências, riscos e recomendações read-only.",
        analysis_mode="SYSTEMIC",
        question_template=(
            "Produza uma análise sistêmica read-only do módulo de origem. "
            "Identifique achados, causas, riscos, evidências ausentes e recomendações, sem executar mudanças."
        ),
        max_output_tokens=1350,
        request_token_limit=14113,
        display_order=10,
    ),
    _profile(
        profile_id="1bc8d45f-6f04-50d8-a94d-14750dc8d113",
        slug="root-cause",
        name="Causa raiz",
        description="Investiga a origem dos desvios e separa causa, sintoma e ausência de evidência.",
        analysis_mode="ROOT_CAUSE_AUDIT",
        question_template=(
            "Execute uma auditoria read-only de causa raiz no módulo de origem. "
            "Separe causas, sintomas, fatores contribuintes, lacunas de evidência e ações seguras recomendadas."
        ),
        max_output_tokens=1350,
        request_token_limit=14113,
        display_order=20,
    ),
    _profile(
        profile_id="7348513b-cadf-5676-812c-4d6cd71bf5f6",
        slug="risk-anomalies",
        name="Riscos e anomalias",
        description="Prioriza inconsistências, conflitos de política e sinais de risco operacional.",
        analysis_mode="SYSTEMIC",
        question_template=(
            "Analise o módulo de origem em modo read-only, priorizando anomalias, conflitos de configuração, "
            "riscos operacionais, qualidade dos dados e condições que exigem intervenção humana."
        ),
        max_output_tokens=1350,
        request_token_limit=14113,
        display_order=30,
    ),
)


def upgrade() -> None:
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    table = op.create_table(
        "ai_analysis_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("analysis_mode", sa.String(40), nullable=False),
        sa.Column("authority", sa.String(40), nullable=False),
        sa.Column("question_template", sa.Text(), nullable=False),
        sa.Column("max_cost_usd", sa.Numeric(18, 8), nullable=False),
        sa.Column("input_cost_per_million", sa.Numeric(18, 8), nullable=False),
        sa.Column("output_cost_per_million", sa.Numeric(18, 8), nullable=False),
        sa.Column("max_input_tokens", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("request_token_limit", sa.Integer(), nullable=False),
        sa.Column("daily_token_limit", sa.Integer(), nullable=False),
        sa.Column("monthly_token_limit", sa.Integer(), nullable=False),
        sa.Column("pricing_source_url", sa.Text(), nullable=False),
        sa.Column("pricing_observed_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("pricing_valid_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("profile_hash", sa.String(64), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint("slug", name="uq_ai_analysis_profile_slug"),
        sa.UniqueConstraint("profile_hash", name="uq_ai_analysis_profile_hash"),
        sa.CheckConstraint("provider IN ('anthropic', 'openai', 'gemini')", name="ck_ai_analysis_profile_provider"),
        sa.CheckConstraint("analysis_mode IN ('LOCAL', 'SYSTEMIC', 'ROOT_CAUSE_AUDIT')", name="ck_ai_analysis_profile_mode"),
        sa.CheckConstraint("authority = 'ANALYSIS_ONLY'", name="ck_ai_analysis_profile_authority"),
        sa.CheckConstraint("max_cost_usd > 0", name="ck_ai_analysis_profile_cost"),
        sa.CheckConstraint("input_cost_per_million >= 0 AND output_cost_per_million >= 0", name="ck_ai_analysis_profile_prices"),
        sa.CheckConstraint("max_input_tokens > 0 AND max_output_tokens > 0", name="ck_ai_analysis_profile_tokens"),
        sa.CheckConstraint("request_token_limit >= max_input_tokens + max_output_tokens", name="ck_ai_analysis_profile_request_budget"),
        sa.CheckConstraint("daily_token_limit >= request_token_limit", name="ck_ai_analysis_profile_daily_budget"),
        sa.CheckConstraint("monthly_token_limit >= daily_token_limit", name="ck_ai_analysis_profile_monthly_budget"),
        sa.CheckConstraint("pricing_valid_until > pricing_observed_at", name="ck_ai_analysis_profile_pricing_window"),
    )
    op.create_index("ix_ai_analysis_profile_active_order", "ai_analysis_profiles", ["is_active", "display_order"])
    op.bulk_insert(table, list(PROFILES))
    op.add_column(
        "ai_model_approvals",
        sa.Column("approval_method", sa.String(40), nullable=False, server_default="HUMAN_PHRASE"),
    )
    op.add_column(
        "ai_model_approvals",
        sa.Column("analysis_profile_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_model_approval_analysis_profile",
        "ai_model_approvals",
        "ai_analysis_profiles",
        ["analysis_profile_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_ai_model_approval_analysis_profile", "ai_model_approvals", type_="foreignkey")
    op.drop_column("ai_model_approvals", "analysis_profile_id")
    op.drop_column("ai_model_approvals", "approval_method")
    op.drop_index("ix_ai_analysis_profile_active_order", table_name="ai_analysis_profiles")
    op.drop_table("ai_analysis_profiles")
