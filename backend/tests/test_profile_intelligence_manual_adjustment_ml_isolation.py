"""Contract proof that the manual rail cannot write ML/capture domains."""

import ast
from pathlib import Path

from app.models.profile_intelligence_manual import ProfileIntelligenceManualAdjustment


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "app" / "services" / "profile_intelligence_manual_service.py"
MIGRATION = ROOT / "alembic" / "versions" / "137_profile_intelligence_manual_adjustments.py"


def test_manual_service_has_no_ml_capture_or_autopilot_imports():
    tree = ast.parse(SERVICE.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom): imports.append(node.module or "")
    assert not any(token in name for name in imports for token in ("shadow_trade", ".ml", "autopilot"))


def test_manual_sql_never_targets_ml_or_historical_tables():
    source = SERVICE.read_text(encoding="utf-8").lower()
    forbidden_writes = [
        "insert into shadow_trades", "update shadow_trades", "delete from shadow_trades",
        "insert into ml_", "update ml_", "delete from ml_",
        "insert into model_", "update model_", "delete from model_",
    ]
    assert not any(statement in source for statement in forbidden_writes)


def test_additive_migration_does_not_reference_capture_or_model_tables():
    source = MIGRATION.read_text(encoding="utf-8").lower()
    for table in ("shadow_trades", "ml_model_registry", "ml_training_runs", "model_versions"):
        assert table not in source


def test_persisted_safety_flags_are_fail_closed():
    table = ProfileIntelligenceManualAdjustment.__table__
    assert table.c.autopilot_applied.default.arg is False
    assert table.c.ml_training_mutated.default.arg is False
    assert table.c.historical_dataset_mutated.default.arg is False
