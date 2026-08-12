from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai_orchestration.hashing import canonical_hash
from app.api.ai_modules import (
    CreateProfileAnalysisRequest,
    _validate_profile,
    analysis_profile_snapshot,
)


BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
MIGRATION = BACKEND / "alembic/versions/155_ai_analysis_profiles.py"


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_155_ai_analysis_profiles", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _output_budget_migration_module():
    migration_path = BACKEND / "alembic/versions/160_systemic_output_budget.py"
    spec = importlib.util.spec_from_file_location("migration_160_systemic_output_budget", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seeded_profiles_are_hash_exact_and_analysis_only():
    migration = _migration_module()
    assert len(migration.PROFILES) == 3
    for seeded in migration.PROFILES:
        profile = SimpleNamespace(**seeded)
        assert canonical_hash(analysis_profile_snapshot(profile)) == seeded["profile_hash"]
        assert seeded["authority"] == "ANALYSIS_ONLY"
        assert seeded["analysis_mode"] in {"LOCAL", "SYSTEMIC", "ROOT_CAUSE_AUDIT"}
        worst_case = (
            seeded["max_input_tokens"] * seeded["input_cost_per_million"]
            + seeded["max_output_tokens"] * seeded["output_cost_per_million"]
        ) / 1_000_000
        assert worst_case <= seeded["max_cost_usd"]


def test_output_budget_migration_promotes_the_complete_governed_budget():
    migration = _output_budget_migration_module()
    seeded = dict(_migration_module().PROFILES[0])
    snapshot = migration._profile_snapshot(
        seeded,
        max_output_tokens=2300,
        profile_version=2,
        enforce_governed_budget=True,
    )
    assert snapshot["max_cost_usd"] == "0.45000000"
    assert snapshot["max_input_tokens"] == 200000
    assert snapshot["max_output_tokens"] == 2300
    assert snapshot["request_token_limit"] == 444600
    worst_case = (
        snapshot["max_input_tokens"] * float(snapshot["input_cost_per_million"])
        + snapshot["max_output_tokens"] * float(snapshot["output_cost_per_million"])
    ) / 1_000_000
    assert worst_case <= float(snapshot["max_cost_usd"])


def test_output_budget_repair_revision_fits_live_alembic_version_column():
    migration_path = BACKEND / "alembic/versions/161_output_budget_repair.py"
    spec = importlib.util.spec_from_file_location("migration_161_output_budget_repair", migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert len(migration.revision) <= 32
    assert migration.down_revision == "160_systemic_output_budget"


def test_profile_validation_fails_closed_on_expired_pricing():
    migration = _migration_module()
    seeded = dict(migration.PROFILES[0])
    seeded["pricing_valid_until"] = datetime(2026, 8, 9, tzinfo=timezone.utc)
    profile = SimpleNamespace(**seeded)

    with pytest.raises(HTTPException) as caught:
        _validate_profile(profile, datetime(2026, 8, 10, tzinfo=timezone.utc))

    assert caught.value.detail == {"code": "ANALYSIS_PROFILE_PRICING_EXPIRED"}


def test_frontend_uses_profile_selection_without_technical_cost_form():
    source = (REPO / "frontend/components/ai/ModuleAIAnalysisAction.tsx").read_text(encoding="utf-8")
    assert "/ai/modules/analysis-profiles" in source
    assert "/ai/modules/analysis-runs/from-profile" in source
    assert "Escolha o perfil da análise" in source
    assert "Prompt da análise" in source
    assert "user_prompt: prompt.trim()" in source
    assert "!selectedProfile || !promptReady" in source
    for removed_label in (
        "Máximo de tokens de entrada",
        "Limite diário de tokens",
        "Fonte oficial do preço",
        "Confirmação humana",
    ):
        assert removed_label not in source


def test_profile_run_normalizes_user_prompt_and_preserves_legacy_client():
    payload = CreateProfileAnalysisRequest(
        origin_module="strategy_profiles",
        origin_view="profiles",
        analysis_profile_id="44dd0065-7de7-5b1d-bfe1-c7a5f008c9a1",
        user_prompt="  Compare os perfis ativos.  ",
        idempotency_key="module-analysis-profile-1234567890",
    )
    assert payload.user_prompt == "Compare os perfis ativos."

    legacy_payload = CreateProfileAnalysisRequest(
        origin_module="strategy_profiles",
        origin_view="profiles",
        analysis_profile_id="44dd0065-7de7-5b1d-bfe1-c7a5f008c9a1",
        idempotency_key="module-analysis-profile-1234567890",
    )
    assert legacy_payload.user_prompt is None

    with pytest.raises(ValueError):
        CreateProfileAnalysisRequest(
            origin_module="strategy_profiles",
            origin_view="profiles",
            analysis_profile_id="44dd0065-7de7-5b1d-bfe1-c7a5f008c9a1",
            user_prompt="   ",
            idempotency_key="module-analysis-profile-1234567890",
        )


def test_profile_selection_is_persisted_as_approval_provenance():
    api_source = (BACKEND / "app/api/ai_modules.py").read_text(encoding="utf-8")
    model_source = (BACKEND / "app/models/systemic_ai.py").read_text(encoding="utf-8")
    assert 'PROFILE_APPROVAL_METHOD = "PREDEFINED_PROFILE"' in api_source
    assert "analysis_profile_id=profile.id" in api_source
    assert "approval_method=PROFILE_APPROVAL_METHOD" in api_source
    assert "approval_method = Column" in model_source
    assert "analysis_profile_id = Column" in model_source
