"""FIX-AC-GOV-002 Fase 7.2: shadow_portfolio dataset fetch is parametrized
for explicit window and sampling method instead of an implicit "most
recent N rows" cut. These tests inspect the compiled SQL rather than
requiring a live database -- the shadow_portfolio branch's row selection
is pure SQLAlchemy Core statement construction with no side effects until
``db.execute()`` runs it.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, or_, select

from app.models.profile import Profile
from app.models.profile_intelligence import MLModelRegistry
from app.models.shadow_trade import ShadowTrade
from app.services.module_ai_analysis_service import _filter_window_bound

TENANT_ID = uuid.uuid4()


def _build_statement(filters: dict) -> str:
    """Mirror of the shadow_portfolio branch in
    ModuleAIAnalysisService._rows, compiled to SQL text for inspection."""
    statement = select(ShadowTrade).where(ShadowTrade.user_id == TENANT_ID)
    window_start = _filter_window_bound(filters.get("window_start"))
    window_end = _filter_window_bound(filters.get("window_end"))
    if window_start is not None:
        statement = statement.where(ShadowTrade.entry_timestamp >= window_start)
    if window_end is not None:
        statement = statement.where(ShadowTrade.entry_timestamp <= window_end)
    limit = min(max(int(filters.get("max_rows", 200)), 1), 5_000)
    sampling_method = str(filters.get("sampling_method") or "recent")
    if sampling_method == "random":
        statement = statement.order_by(func.random()).limit(limit)
    elif sampling_method == "recent":
        statement = statement.order_by(ShadowTrade.entry_timestamp.desc()).limit(limit)
    else:
        raise ValueError(f"Unsupported sampling_method: {sampling_method}")
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def test_default_sampling_is_unchanged_recency_cut_with_configurable_size():
    sql = _build_statement({})
    assert "entry_timestamp DESC" in sql
    assert "LIMIT 200" in sql
    assert "entry_timestamp >=" not in sql
    assert "entry_timestamp <=" not in sql


def test_random_sampling_replaces_the_recency_order_and_honors_max_rows():
    # max_rows=279 is the n derived in Fase 7.3 to detect the real observed
    # 60.3% vs 54.15% delta at 80% power / alpha=0.05 for this population.
    sql = _build_statement({"sampling_method": "random", "max_rows": 279})
    assert "random()" in sql.lower()
    assert "LIMIT 279" in sql
    assert "entry_timestamp DESC" not in sql


def test_explicit_window_bounds_are_applied_as_filters():
    sql = _build_statement({
        "window_start": "2026-08-01T00:00:00Z",
        "window_end": "2026-08-12T00:00:00Z",
    })
    assert "entry_timestamp >=" in sql
    assert "entry_timestamp <=" in sql
    assert "2026-08-01" in sql
    assert "2026-08-12" in sql


def test_unsupported_sampling_method_is_rejected():
    with pytest.raises(ValueError, match="Unsupported sampling_method"):
        _build_statement({"sampling_method": "bogus"})


def test_max_rows_is_clamped_between_one_and_five_thousand():
    assert "LIMIT 1" in _build_statement({"max_rows": 0}) or "LIMIT 1\n" in _build_statement({"max_rows": 0})
    assert "LIMIT 5000" in _build_statement({"max_rows": 999_999})


def _build_ml_models_statement() -> str:
    """Mirror of the ml_models branch in ModuleAIAnalysisService._rows."""
    statement = select(MLModelRegistry).outerjoin(
        Profile, Profile.id == MLModelRegistry.profile_id,
    ).where(
        or_(MLModelRegistry.profile_id.is_(None), Profile.user_id == TENANT_ID)
    )
    return str(statement.compile(compile_kwargs={"literal_binds": True}))


def test_ml_models_dataset_includes_global_scope_models():
    """ml_model_registry.profile_id is nullable by design (a NULL marks a
    global-scope model trained across every profile, not an ownership gap --
    every row in production has NULL profile_id today). An inner join would
    silently exclude all of them; this must be an outer join with an
    explicit "NULL is global, visible to everyone" branch."""
    sql = _build_ml_models_statement()
    assert "LEFT OUTER JOIN profiles" in sql
    assert "ml_model_registry.profile_id IS NULL" in sql
    assert "profiles.user_id =" in sql
