from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.association_rules_service import _association_actionability
from app.services.counterfactual_combination_service import (
    _evaluate_rules,
    _missing_features_count,
)
from app.services.optuna_profile_search_service import _optuna_validation_status
from app.services.profile_validation_service import (
    classify_validation,
    suggestion_actionable,
    temporal_split_valid,
)


def _windows():
    discovery_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    discovery_end = discovery_start + timedelta(days=70)
    validation_start = discovery_end + timedelta(microseconds=1)
    validation_end = validation_start + timedelta(days=30)
    return discovery_start, discovery_end, validation_start, validation_end


def _metrics(
    total_cases,
    win_rate,
    lift,
    *,
    base_win_rate=0.50,
    symbols=4,
    days=5,
    symbol_share=0.30,
    day_share=0.30,
):
    return {
        "total_cases": total_cases,
        "win_rate": win_rate,
        "base_win_rate": base_win_rate,
        "lift": lift,
        "distinct_symbols": symbols,
        "distinct_days": days,
        "max_single_symbol_share": symbol_share,
        "max_single_day_share": day_share,
    }


def test_dynamic_without_validation_is_not_actionable():
    ds, de, vs, ve = _windows()
    result = classify_validation(
        discovery_metrics=_metrics(60, 0.72, 1.44),
        validation_metrics=_metrics(0, 0, 0),
        discovery_start=ds,
        discovery_end=de,
        validation_start=vs,
        validation_end=ve,
    )

    assert result["actionability_status"] == "exploratory_only"
    assert suggestion_actionable(
        "counterfactual_dynamic",
        result,
    ) == (False, "blocked_no_validation")


def test_dynamic_bad_validation_lift_is_blocked():
    ds, de, vs, ve = _windows()
    result = classify_validation(
        discovery_metrics=_metrics(60, 0.72, 1.44),
        validation_metrics=_metrics(30, 0.56, 1.05),
        discovery_start=ds,
        discovery_end=de,
        validation_start=vs,
        validation_end=ve,
    )

    assert result["blocked_reason"] == "blocked_validation_lift"


def test_missing_feature_never_passes_dynamic_rule():
    rules = [{"indicator": "rsi", "operator": ">=", "value": 40}]

    assert _evaluate_rules({}, rules) is False
    assert _missing_features_count({}, rules) == 1


def test_validated_dynamic_can_generate_candidate():
    ds, de, vs, ve = _windows()
    result = classify_validation(
        discovery_metrics=_metrics(60, 0.72, 1.44),
        validation_metrics=_metrics(30, 0.62, 1.24),
        discovery_start=ds,
        discovery_end=de,
        validation_start=vs,
        validation_end=ve,
    )

    assert result["validation_status"] == "validated"
    assert result["actionability_status"] == "validated"
    assert suggestion_actionable(
        "counterfactual_dynamic",
        result,
    ) == (True, None)


def test_association_loss_never_becomes_positive_signal():
    assert _association_actionability(
        ["LOSS"],
        "validated",
    ) == "block_rule_candidate"
    assert _association_actionability(
        ["SL_HIT"],
        "validated",
    ) != "positive_signal_candidate"


def test_association_without_validation_is_exploratory():
    status = _association_actionability(
        ["WIN"],
        "blocked_no_validation",
    )

    assert status == "exploratory_only"
    assert suggestion_actionable(
        "association_rule",
        {
            "validation_status": "blocked_no_validation",
            "actionability_status": status,
        },
    ) == (False, "blocked_no_validation")


def test_temporal_validation_requires_non_overlapping_ordered_windows():
    ds, de, vs, ve = _windows()

    assert temporal_split_valid(ds, de, vs, ve)
    assert not temporal_split_valid(ds, de, de, ve)


def test_optuna_overfit_status_is_blocked():
    assert _optuna_validation_status({
        "validation_status": "blocked_validation_lift",
        "blocked_reason": "blocked_validation_lift",
    }) == "optuna_blocked_overfit_risk"


def test_optuna_source_uses_validation_window_and_metrics():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "optuna_profile_search_service.py"
    ).read_text(encoding="utf-8")

    assert "validation_start" in source
    assert "validation_end" in source
    assert "validation_trades" in source
    assert "validation_metrics_json=validation_metrics" in source


def test_ui_does_not_offer_actionable_suggestion_without_validation():
    frontend = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "app"
        / "profile-intelligence"
        / "page.tsx"
    ).read_text(encoding="utf-8")

    assert "EXPLORATÓRIO" in frontend
    assert "BLOQUEADO" in frontend
    assert "Sugestão bloqueada por validation" in frontend
    assert "disabled={generatingSuggestion || !combinationIsActionable" in frontend
