"""Regression proof that Profile Intelligence AI v2 cannot mutate ML or capture data."""

from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (BACKEND / path).read_text(encoding="utf-8").lower()


def test_profile_intelligence_ai_v2_has_no_training_or_dataset_dependencies():
    sources = "\n".join(
        (
            _source("app/services/profile_intelligence_analysis_v2.py"),
            _source("app/services/profile_intelligence_ai_models.py"),
        )
    )
    forbidden = (
        "run_lgbm",
        "run_catboost",
        "xgboost",
        "lightgbm",
        "catboost",
        "dataset_builder",
        "feature_extractor",
        "ml_model_registry",
        "eligible_for_training =",
        "update shadow_trades",
        "delete from shadow_trades",
        "insert into shadow_trades",
    )
    assert all(token not in sources for token in forbidden)


def test_profile_intelligence_ai_v2_migration_is_additive_only():
    migration = _source("alembic/versions/139_profile_intelligence_ai_v2.py")
    assert "op.add_column(" in migration
    assert "op.create_table(" in migration
    assert "op.execute(" not in migration
    assert "update " not in migration
    assert "delete from" not in migration
    assert '"profile_score_optimization_runs"' in migration
    assert '"profile_intelligence_ai_model_audit"' in migration
