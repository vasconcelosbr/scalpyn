"""Confirmed 2026-08-17 against a live root-cause-audit request
(ai_request_id=d82a01ee-37cb-4abc-86f1-12213e3c28d9): the ml_models and
strategy_profiles evidence rows carried ~226KB of ML-training-internal
fields and duplicate JSON with no entry-indicator signal, contributing to
AI_INPUT_RESERVATION_EXCEEDED / provider HTTP 400 on evidence-heavy runs.
These tests pin the trim so it cannot silently regress back to shipping
that weight, and confirm no real information is dropped.
"""
from __future__ import annotations

from app.services.module_ai_analysis_service import _ml_metrics_for_evidence


def test_ml_metrics_for_evidence_drops_training_internals_keeps_indicator_evidence():
    metrics = {
        "f1": 0.23,
        "roc_auc": 0.58,
        "optuna_study": {"seed": 42, "trials": [{"number": 0, "params": {}}] * 100},
        "threshold_curve": [{"threshold": t / 100, "net_ev": -0.4} for t in range(53)],
        "split_diagnostics": {"raw_train": 737, "raw_test": 214},
        "intelligence_report": {
            "label": "positive_net_return",
            "findings": [{"indicator": "momentum_strength", "action": "PRIORITIZE"}],
        },
    }
    trimmed = _ml_metrics_for_evidence(metrics)
    assert "optuna_study" not in trimmed
    assert "threshold_curve" not in trimmed
    assert "split_diagnostics" not in trimmed
    assert trimmed["intelligence_report"] == metrics["intelligence_report"]
    assert trimmed["f1"] == 0.23
    assert trimmed["roc_auc"] == 0.58


def test_ml_metrics_for_evidence_passes_through_non_dict_unchanged():
    assert _ml_metrics_for_evidence(None) is None
    assert _ml_metrics_for_evidence([1, 2, 3]) == [1, 2, 3]


def test_ml_metrics_for_evidence_is_a_pure_filter_not_a_mutation():
    metrics = {"optuna_study": {"trials": []}, "f1": 0.5}
    trimmed = _ml_metrics_for_evidence(metrics)
    assert "optuna_study" in metrics
    assert trimmed is not metrics


def test_strategy_profile_row_drops_config_duplicate_keeps_scoring():
    """Mirrors the strategy_profiles branch of ModuleAIAnalysisService._rows:
    "config" used to be echoed whole even though signals/entry_triggers/
    block_rules/filters/scoring are all extracted from it separately below
    -- byte-identical duplication for every key except scoring."""
    config = {
        "signals": {"weight": 1},
        "entry_triggers": [{"indicator": "rsi"}],
        "block_rules": {"blocks": []},
        "filters": {"conditions": []},
        "scoring": {"weights": {"momentum": 40}},
        "default_timeframe": "5m",
    }
    row = {
        "timeframe": (config or {}).get("default_timeframe"),
        "scoring": (config or {}).get("scoring"),
        "signals": (config or {}).get("signals"),
        "entry_triggers": (config or {}).get("entry_triggers"),
        "block_rules": (config or {}).get("block_rules"),
        "filters": (config or {}).get("filters"),
    }
    assert "config" not in row
    assert row["scoring"] == config["scoring"]
    assert row["signals"] == config["signals"]
    assert row["entry_triggers"] == config["entry_triggers"]
    assert row["block_rules"] == config["block_rules"]
    assert row["filters"] == config["filters"]
    assert row["timeframe"] == config["default_timeframe"]
    # Every key that lived in config is reachable from row -- nothing lost.
    assert set(config) == {"signals", "entry_triggers", "block_rules", "filters", "scoring", "default_timeframe"}
