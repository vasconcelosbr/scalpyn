from types import SimpleNamespace
from pathlib import Path

from app.services.algorithm_governance_service import (
    autonomy_block_reason,
    forward_transition_block_reason,
    production_model_block_reason,
    source_profile_attribution,
    suggestion_registry_block_reasons,
)


def test_suggestion_registry_requires_full_traceability():
    suggestion = SimpleNamespace(
        source_type=None,
        source_run_id=None,
        profile_id=None,
        diff_json=None,
        rollback_payload=None,
        validation_status="blocked_no_validation",
        actionability_status="exploratory_only",
    )
    reasons = suggestion_registry_block_reasons(suggestion)
    assert "missing_source_type" in reasons
    assert "missing_source_run_id" in reasons
    assert "missing_profile_id" in reasons
    assert "missing_diff_json" in reasons
    assert "missing_rollback_payload" in reasons
    assert "validation_not_validated" in reasons


def test_validated_suggestion_with_rollback_is_applicable():
    suggestion = SimpleNamespace(
        source_type="counterfactual_dynamic",
        source_run_id="run-1",
        profile_id="profile-1",
        diff_json={"before": None, "after": {"signals": []}},
        rollback_payload={"action": "archive_generated_profile"},
        validation_status="validated",
        actionability_status="validated",
    )
    assert suggestion_registry_block_reasons(suggestion) == []


def test_source_profile_attribution_is_stable_and_deduplicated():
    names, ids = source_profile_attribution([
        {"profile_id": "2", "profile_name": "Trend"},
        {"profile_id": "1", "profile_name": "Bounce"},
        {"profile_id": "2", "profile_name": "Trend"},
    ])
    assert names == ["Bounce", "Trend"]
    assert ids == ["1", "2"]


def test_forward_live_requires_validation_shadow_approval_and_rollback():
    assert forward_transition_block_reason(
        "human_approval",
        "limited_live",
        validation_status="validated",
        shadow_forward_passed=True,
        human_approved=False,
        rollback_available=True,
    ) == "blocked_human_approval_required"
    assert forward_transition_block_reason(
        "human_approval",
        "limited_live",
        validation_status="validated",
        shadow_forward_passed=True,
        human_approved=True,
        rollback_available=True,
    ) is None


def test_autonomy_levels_four_and_five_remain_blocked():
    assert autonomy_block_reason(
        4,
        configured_maximum_level=3,
        forward_validation=True,
        auto_rollback=True,
        impact_limit=True,
        cooldown=True,
        max_changes_per_day=True,
        risk_budget=True,
        post_change_monitoring=True,
    ) == "requested_autonomy_exceeds_policy"


def test_challenger_and_unimplemented_models_cannot_control_production():
    assert production_model_block_reason(
        registry_status="challenger",
        model_type="xgboost",
        operational=True,
    ) == "challenger_cannot_control_production"
    assert production_model_block_reason(
        registry_status="champion",
        model_type="lightgbm",
        operational=False,
    ) == "lightgbm_not_operational"


def test_analyzers_select_and_persist_source_profiles():
    backend = Path(__file__).resolve().parents[1]
    indicator = (
        backend / "app" / "services" / "indicator_lift_service.py"
    ).read_text(encoding="utf-8")
    combinations = (
        backend / "app" / "services" / "counterfactual_combination_service.py"
    ).read_text(encoding="utf-8")
    association = (
        backend / "app" / "services" / "association_rules_service.py"
    ).read_text(encoding="utf-8")
    for source in (indicator, combinations, association):
        assert "profile_id" in source
        assert "profile_name" in source
        assert "source_profile_ids" in source
        assert "source_profiles" in source


def test_optuna_persists_required_validation_metrics():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "optuna_profile_search_service.py"
    ).read_text(encoding="utf-8")
    for field in (
        "validation_expected_pnl",
        "validation_precision",
        "validation_fpr",
        "validation_win_rate_lift",
        "validation_drawdown_reduction",
        "validation_trade_count",
    ):
        assert field in source


def test_registry_migration_enforces_one_champion_per_scope():
    migration = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "097_ml_champion_challenger_registry.py"
    ).read_text(encoding="utf-8")
    assert "uq_ml_registry_one_champion_scope" in migration
    assert "WHERE status = 'champion'" in migration
    assert "uq_production_champion_scope" in migration


def test_decision_payload_records_model_identity():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "tasks"
        / "pipeline_scan.py"
    ).read_text(encoding="utf-8")
    assert '"ml_model_id"' in source
    assert '"ml_model_type"' in source
