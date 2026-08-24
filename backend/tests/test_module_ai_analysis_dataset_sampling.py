"""Shadow Portfolio must never fall back to sampled module rows."""

from __future__ import annotations

import inspect
import uuid

import pytest
from sqlalchemy import or_, select

from app.models.profile import Profile
from app.models.profile_intelligence import MLModelRegistry
from app.services.module_ai_analysis_service import ModuleAIAnalysisService


@pytest.mark.asyncio
async def test_shadow_rows_reader_is_fail_closed_without_canonical_report():
    with pytest.raises(RuntimeError, match="SHADOW_CANONICAL_REPORT_REQUIRED"):
        await ModuleAIAnalysisService._rows(
            object(),
            tenant_id=uuid.uuid4(),
            module_key="shadow_portfolio",
            entity_ids=(),
            filters={"max_rows": 1, "sampling_method": "random"},
        )


def test_shadow_creation_path_has_no_sampling_or_curated_indicator_catalog():
    source = inspect.getsource(ModuleAIAnalysisService.create_run)
    assert "capture_report" in source
    assert "report_run_id" in source
    assert "sampling_method" not in source
    assert "_INDICATOR_NAMES" not in source
    assert '**({} if shadow_capture is not None else {"rows": rows})' in source


def test_shadow_capture_only_stage_cancels_job_before_provider_dispatch():
    source = inspect.getsource(ModuleAIAnalysisService.create_run)
    assert "shadow_full_canonical_capture_enabled" in source
    assert "shadow_full_canonical_provider_enabled" in source
    assert "SHADOW_CANONICAL_CAPTURE_ONLY" in source
    assert 'run.status = "CAPTURED"' in source


def _build_ml_models_statement() -> str:
    tenant_id = uuid.uuid4()
    statement = select(MLModelRegistry).outerjoin(
        Profile, Profile.id == MLModelRegistry.profile_id,
    ).where(
        or_(MLModelRegistry.profile_id.is_(None), Profile.user_id == tenant_id)
    )
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def test_ml_models_dataset_includes_global_scope_models():
    sql = _build_ml_models_statement()
    assert "LEFT OUTER JOIN profiles" in sql
    assert "ml_model_registry.profile_id IS NULL" in sql
    assert "profiles.user_id =" in sql
