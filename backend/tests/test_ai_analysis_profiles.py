from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.ai_orchestration.hashing import canonical_hash
from app.api.ai_modules import analysis_profile_snapshot, _validate_profile


BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
MIGRATION = BACKEND / "alembic/versions/155_ai_analysis_profiles.py"


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_155_ai_analysis_profiles", MIGRATION)
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
    for removed_label in (
        "Máximo de tokens de entrada",
        "Limite diário de tokens",
        "Fonte oficial do preço",
        "Confirmação humana",
    ):
        assert removed_label not in source


def test_profile_selection_is_persisted_as_approval_provenance():
    api_source = (BACKEND / "app/api/ai_modules.py").read_text(encoding="utf-8")
    model_source = (BACKEND / "app/models/systemic_ai.py").read_text(encoding="utf-8")
    assert 'PROFILE_APPROVAL_METHOD = "PREDEFINED_PROFILE"' in api_source
    assert "analysis_profile_id=profile.id" in api_source
    assert "approval_method=PROFILE_APPROVAL_METHOD" in api_source
    assert "approval_method = Column" in model_source
    assert "analysis_profile_id = Column" in model_source
