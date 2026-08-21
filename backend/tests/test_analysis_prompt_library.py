from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
import pytest

from app.api.ai_modules import CreateProfileAnalysisRequest, _compose_profile_question
from app.api.analysis_prompts import PromptVersionPayload, content_hash, require_admin
from app.ai_orchestration.initial_prompts import initial_prompt_registry


REPO = Path(__file__).resolve().parents[2]


def _migration_module():
    path = REPO / "backend/alembic/versions/193_analysis_prompt_library.py"
    spec = importlib.util.spec_from_file_location("migration_193", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _systemic_contract_migration_module():
    path = REPO / "backend/alembic/versions/194_systemic_prompt_input_contract.py"
    spec = importlib.util.spec_from_file_location("migration_194", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _text_tolerant_migration_module():
    path = REPO / "backend/alembic/versions/195_text_tolerant_systemic_prompt.py"
    spec = importlib.util.spec_from_file_location("migration_195", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_seeded_markdown_is_exact_and_reproducible():
    migration = _migration_module()
    content = migration._seed_content()
    assert len(content) == 23_739
    assert len(content.encode("utf-8")) == 24_282
    assert content_hash(content) == migration.PROMPT_HASH
    assert content.startswith("# SCALPYN")


def test_systemic_input_contract_migration_matches_immutable_registry_version():
    migration = _systemic_contract_migration_module()
    values = migration._prompt_values()
    prompt = initial_prompt_registry().resolve("systemic-multimodule", "2.0.6")

    assert len(migration.revision) <= 32
    assert values["id"] == prompt.id
    assert values["content_hash"] == prompt.content_hash == migration.EXPECTED_PROMPT_HASH
    assert values["system_template"] == prompt.system_template
    assert values["user_template"] == prompt.user_template


def test_text_tolerant_contract_migration_matches_immutable_registry_version():
    migration = _text_tolerant_migration_module()
    values = migration._prompt_values()
    prompt = initial_prompt_registry().resolve("systemic-multimodule", "2.0.7")

    assert len(migration.revision) <= 32
    assert migration.down_revision == "194_systemic_prompt_contract"
    assert values["id"] == prompt.id
    assert values["content_hash"] == prompt.content_hash == migration.EXPECTED_PROMPT_HASH
    assert values["system_template"] == prompt.system_template
    assert values["user_template"] == prompt.user_template


def test_prompt_payload_normalizes_markdown_and_validates_upload():
    payload = PromptVersionPayload(
        name="  Auditor   L3  ",
        description="  descrição  ",
        content_markdown="\ufefflinha 1\r\nlinha 2",
        source_type="UPLOAD_MD",
        source_filename="AUDITOR.MD",
    )
    assert payload.name == "Auditor L3"
    assert payload.description == "descrição"
    assert payload.content_markdown == "linha 1\nlinha 2"

    with pytest.raises(ValidationError, match="source_filename"):
        PromptVersionPayload(
            name="Auditor",
            content_markdown="conteúdo válido",
            source_type="UPLOAD_MD",
            source_filename="prompt.txt",
        )

    with pytest.raises(ValidationError, match="256 KiB"):
        PromptVersionPayload(
            name="Auditor",
            content_markdown="😀" * 70_000,
            source_type="PASTE",
        )

    with pytest.raises(ValidationError):
        PromptVersionPayload(
            name="Auditor",
            content_markdown="x" * 100_001,
            source_type="PASTE",
        )


def test_profile_run_accepts_saved_prompt_and_optional_complement():
    version_id = uuid4()
    payload = CreateProfileAnalysisRequest(
        origin_module="shadow_portfolio",
        origin_view="shadow-portfolio-detailed-report",
        analysis_profile_id=uuid4(),
        analysis_prompt_version_id=version_id,
        prompt_complement="  Compare apenas esta amostra.  ",
        idempotency_key="module-analysis-profile-1234567890",
    )
    assert payload.analysis_prompt_version_id == version_id
    assert payload.prompt_complement == "Compare apenas esta amostra."

    with pytest.raises(ValidationError, match="legacy-only"):
        CreateProfileAnalysisRequest(
            origin_module="shadow_portfolio",
            origin_view="shadow-portfolio-detailed-report",
            analysis_profile_id=uuid4(),
            analysis_prompt_version_id=version_id,
            user_prompt="cliente antigo",
            idempotency_key="module-analysis-profile-1234567890",
        )


def test_saved_prompt_composition_is_deterministic_and_bounded():
    version = SimpleNamespace(
        name_snapshot="Auditor L3",
        version_number=3,
        content_hash="a" * 64,
        content_markdown="# Regras\nCompare SL contra TP.",
    )
    question = _compose_profile_question(
        "Método sistêmico.",
        prompt_version=version,
        prompt_complement="Somente a amostra atual.",
        legacy_user_prompt=None,
    )
    assert question == (
        "Método sistêmico.\n\n"
        f"Prompt de análise selecionado — Auditor L3 (v3, sha256:{'a' * 64}):\n"
        "# Regras\nCompare SL contra TP.\n\n"
        "Complemento específico desta execução:\nSomente a amostra atual."
    )

    oversized = SimpleNamespace(
        name_snapshot="Grande",
        version_number=1,
        content_hash="b" * 64,
        content_markdown="x" * 140_000,
    )
    with pytest.raises(HTTPException) as caught:
        _compose_profile_question(
            "Método",
            prompt_version=oversized,
            prompt_complement=None,
            legacy_user_prompt=None,
        )
    assert caught.value.detail == {"code": "ANALYSIS_PROMPT_EFFECTIVE_QUESTION_TOO_LARGE"}


@pytest.mark.asyncio
async def test_prompt_management_is_admin_only():
    user_id = uuid4()
    db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(is_active=True, role="trader")))
    with pytest.raises(HTTPException) as caught:
        await require_admin(db, user_id)
    assert caught.value.status_code == 403
    assert caught.value.detail == {"code": "ADMIN_ACCESS_REQUIRED"}

    db.get = AsyncMock(return_value=SimpleNamespace(is_active=True, role="admin"))
    user = await require_admin(db, user_id)
    assert user.role == "admin"


def test_request_and_context_persist_prompt_lineage_separately():
    model_source = (REPO / "backend/app/models/systemic_ai.py").read_text(encoding="utf-8")
    persistence_source = (REPO / "backend/app/ai_orchestration/persistence.py").read_text(encoding="utf-8")
    graph_source = (REPO / "backend/app/api/ai_graphs.py").read_text(encoding="utf-8")
    module_api_source = (REPO / "backend/app/api/ai_modules.py").read_text(encoding="utf-8")
    assert "analysis_prompt_version_id = Column" in model_source
    assert "analysis_prompt_version_id=value.analysis_prompt_version_id" in persistence_source
    assert '"analysis_prompt": {' in graph_source
    assert 'analysis_prompt.status != "ACTIVE"' in module_api_source
    assert "analysis_prompt.current_version_id != analysis_prompt_version.id" in module_api_source


def test_shared_selector_covers_every_existing_module_entrypoint():
    component_source = (REPO / "frontend/components/ai/ModuleAIAnalysisAction.tsx").read_text(encoding="utf-8")
    assert "/ai/modules/analysis-prompts" in component_source
    assert "selectedPromptVersionId" in component_source
    for relative_path in (
        "frontend/components/shadow-portfolio/DetailedReportWorkspace.tsx",
        "frontend/app/intelligence-runs/page.tsx",
        "frontend/app/profiles/page.tsx",
        "frontend/app/ml-models/page.tsx",
        "frontend/app/settings/risk/page.tsx",
        "frontend/app/settings/score/page.tsx",
        "frontend/app/settings/social-score/page.tsx",
        "frontend/app/settings/strategies/page.tsx",
    ):
        assert "ModuleAIAnalysisAction" in (REPO / relative_path).read_text(encoding="utf-8")
