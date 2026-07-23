from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.profile_intelligence_analysis_v2 import (
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_SKILL_VERSION,
    canonical_trade_key,
    build_bounded_ai_context,
    confusion_matrix,
    deduplicate_rows,
    select_simulated_points,
    simulate_points,
    validate_ai_response_against_payload,
    validate_analysis_payload,
)
from app.services.profile_intelligence_ai_models import (
    DEFAULT_AI_MODEL,
    SUPPORTED_AI_MODELS,
    configured_model,
)
from app.services.profile_score_optimization_service import (
    DEFAULT_POLICY,
    ProfileScoreOptimizationService,
)


def _row(source, outcome, *, event_id=None, decision_id=None, profile_id=None):
    return {
        "id": uuid4(),
        "source": source,
        "outcome": outcome,
        "event_id": event_id,
        "decision_id": decision_id,
        "ranking_id": None,
        "profile_id": profile_id,
        "symbol": "BTCUSDT",
        "created_at": datetime.now(timezone.utc),
        "pnl_pct": 1 if outcome == "TP_HIT" else -1,
    }


def test_canonical_key_priority_and_cross_source_dedup():
    event = uuid4()
    decision = uuid4()
    row = _row("L3", "TP_HIT", event_id=event, decision_id=decision)
    assert canonical_trade_key(row) == f"decision_id:{decision}"

    duplicate = {**row, "id": uuid4(), "source": "L3_LAB"}
    deduplicated, diagnostics = deduplicate_rows([row, duplicate])
    assert len(deduplicated) == 1
    assert diagnostics["duplicate_rows_removed"] == 1
    assert diagnostics["heuristic_fallback_used"] is False


def test_missing_canonical_key_hard_blocks_ai_payload():
    _, diagnostics = deduplicate_rows([_row("L3_REJECTED", "TP_HIT")])
    payload = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "deduplication": diagnostics,
        "truncated": False,
        "source_metrics": {},
        "confusion_matrix": {"tp": 0, "fp": 0, "fn": 0, "tn": 0},
        "candidates": [],
    }
    validation = validate_analysis_payload(payload)
    assert validation["valid"] is False
    assert "BLOCKED_CROSS_SOURCE_DEDUP_UNAVAILABLE" in validation["hard_errors"]


def test_analysis_fail_fast_does_not_build_candidates_when_dedup_is_unavailable(monkeypatch):
    import app.services.profile_score_optimization_service as module

    monkeypatch.setattr(
        module,
        "_get_indicator_buckets",
        lambda: (_ for _ in ()).throw(AssertionError("candidate scan must not run")),
    )
    evidence, candidates, _ = ProfileScoreOptimizationService()._build_analysis(
        [_row("L3_REJECTED", "TP_HIT")],
        [],
        DEFAULT_POLICY,
        30,
        datetime.now(timezone.utc),
        False,
    )
    assert candidates == []
    assert evidence["pre_ai_validation"]["valid"] is False
    assert evidence["candidate_accounting"]["mutation_instances"] == 0


def test_confusion_matrix_uses_approval_as_prediction_and_tp_as_actual():
    rows = [
        _row("L3", "TP_HIT", event_id=uuid4()),
        _row("L3_LAB", "SL_HIT", event_id=uuid4()),
        _row("L3_REJECTED", "TP_HIT", event_id=uuid4()),
        _row("L3_REJECTED", "TIMEOUT", event_id=uuid4()),
    ]
    matrix = confusion_matrix(rows)
    assert (matrix["tp"], matrix["fp"], matrix["fn"], matrix["tn"]) == (1, 1, 1, 1)
    assert matrix["precision"] == 0.5
    assert matrix["recall"] == 0.5


def test_validator_rejects_tautological_outcome_cohort():
    payload = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "deduplication": {"missing_canonical_key_rows": 0},
        "truncated": False,
        "source_metrics": {},
        "confusion_matrix": {"tp": 1, "fp": 0, "fn": 0, "tn": 0},
        "candidates": [],
        "cohorts": {
            "approved_tp": {
                "definition": "outcome=TP_HIT",
                "metrics": {"closed": 2, "tp": 2, "sl": 0, "timeout": 0},
            }
        },
    }
    validation = validate_analysis_payload(payload)
    assert "TAUTOLOGICAL_OUTCOME_COHORT:approved_tp" in validation["hard_errors"]


def test_ai_guard_rejects_cross_profile_candidate_selection():
    profile_a, profile_b = str(uuid4()), str(uuid4())
    payload = {
        "candidates": [
            {
                "profile_id": profile_a,
                "candidate_id": "candidate-a",
                "scope": "PROFILE",
                "validation": {"status": "VALIDATED"},
                "sources": ["L3"],
            }
        ]
    }
    response = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "executive_summary": "ok",
        "global_diagnosis": [],
        "profile_recommendations": [{
            "profile_id": profile_b,
            "diagnosis": "invalid",
            "selected_candidate_ids": ["candidate-a"],
        }],
        "risks": [],
        "safeguards": [],
    }
    with pytest.raises(ValueError, match="cross_profile"):
        validate_ai_response_against_payload(response, payload)


def test_ai_guard_rejects_numeric_claim_absent_from_deterministic_payload():
    profile_id = str(uuid4())
    payload = {
        "row_count": 100,
        "global_baseline": {"tp_rate": 0.52},
        "candidates": [
            {
                "profile_id": profile_id,
                "candidate_id": "candidate-a",
                "scope": "PROFILE",
                "validation": {"status": "VALIDATED"},
                "sources": ["L3"],
            }
        ],
    }
    response = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "executive_summary": "A taxa verificada é 52%, mas 9999 casos não existem.",
        "global_diagnosis": [],
        "profile_recommendations": [],
        "risks": [],
        "safeguards": [],
    }
    with pytest.raises(ValueError, match="NUMERIC_OR_SCOPE_MISMATCH"):
        validate_ai_response_against_payload(response, payload)


def test_ai_guard_accepts_numeric_claims_present_in_payload():
    payload = {
        "row_count": 100,
        "global_baseline": {"tp_rate": 0.52},
        "candidates": [],
    }
    response = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "executive_summary": "Foram verificados 100 casos com taxa de 52%.",
        "global_diagnosis": [],
        "profile_recommendations": [],
        "risks": [],
        "safeguards": [],
    }
    clean = validate_ai_response_against_payload(response, payload)
    assert clean["executive_summary"].startswith("Foram verificados")


def test_ai_guard_accepts_display_rounding_but_not_new_target():
    payload = {
        "row_count": 100,
        "global_baseline": {"tp_rate": 0.51437, "avg_pnl_pct": -25.43},
        "candidates": [],
    }
    response = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "executive_summary": "Taxa verificada de 51.4% e PnL de -25.4%.",
        "global_diagnosis": [],
        "profile_recommendations": [],
        "risks": [],
        "safeguards": [],
    }
    validate_ai_response_against_payload(response, payload)
    response["executive_summary"] += " Meta nova de 85%."
    with pytest.raises(ValueError, match="NUMERIC_OR_SCOPE_MISMATCH"):
        validate_ai_response_against_payload(response, payload)


def test_bounded_ai_context_keeps_candidates_once_and_omits_provider_policy():
    payload = {
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "analysis_skill_version": ANALYSIS_SKILL_VERSION,
        "policy": {"ai_model_capabilities": {"large": "x" * 10000}},
        "candidates": [{
            "candidate_id": "c1",
            "candidate_definition_id": "d1",
            "scope": "PROFILE",
            "profile_id": "p1",
            "profile_name": "P1",
            "action_type": "ADD_SCORE_PENALTY",
            "target_path": "/scoring/generated_rules",
            "proposed_value": {"points": -5},
            "sources": ["L3"],
            "discovery": {},
            "validation": {"status": "VALIDATED"},
            "simulations": [],
        }],
        "counterfactual_analysis": {"buckets": []},
        "overlap_analysis": [],
    }
    context = build_bounded_ai_context(payload)
    assert [item["candidate_id"] for item in context["candidates"]] == ["c1"]
    assert "policy" not in context
    assert context["bounded_context"]["char_count"] < 10000


def test_model_allowlist_and_default_are_exact_and_no_unknown_fallback():
    assert DEFAULT_AI_MODEL == "claude-haiku-4-5-20251001"
    assert set(SUPPORTED_AI_MODELS) == {
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
    }
    assert configured_model({}) == DEFAULT_AI_MODEL
    with pytest.raises(ValueError, match="BLOCKED_MODEL_UNAVAILABLE"):
        configured_model({"ai_model": "unknown"})


def test_signal_score_threshold_drives_simulation_and_not_default_penalty():
    bucket = {
        "indicator": "volume_spike",
        "condition": lambda value: value >= 2.0,
    }
    rows = [
        {
            "outcome": "TP_HIT",
            "score": None,
            "features_snapshot": {"score": 72, "volume_spike": 1.0},
        },
        {
            "outcome": "SL_HIT",
            "score": None,
            "features_snapshot": {"score": 70, "volume_spike": 3.0},
        },
        {
            "outcome": "SL_HIT",
            "score": None,
            "features_snapshot": {"score": 80, "volume_spike": 3.0},
        },
    ]
    config = {
        "signals": {
            "conditions": [{"field": "score", "operator": ">=", "value": 70}]
        }
    }
    simulations = simulate_points(
        rows,
        bucket,
        (0, -1, -3, -5),
        config,
        lambda row: row["features_snapshot"],
    )
    minus_one = next(item for item in simulations if item["points"] == -1)
    assert minus_one["status"] == "SIMULATED"
    assert minus_one["impact"]["sl_avoided"] == 1
    assert minus_one["impact"]["tp_lost"] == 0
    assert minus_one["impact"]["score_minimum"] == 70
    assert select_simulated_points(
        simulations,
        {
            "score_global_replay_min_retention": 0.60,
            "score_global_replay_max_tp_loss_rate": 0.05,
            "score_global_replay_min_sl_reduction_rate": 0.02,
        },
    ) == -1


def test_simulation_without_score_baseline_cannot_choose_default_minus_five():
    simulations = simulate_points(
        [{"outcome": "SL_HIT", "features_snapshot": {"volume_spike": 3.0}}],
        {"indicator": "volume_spike", "condition": lambda value: value >= 2.0},
        (0, -1, -5),
        {},
        lambda row: row["features_snapshot"],
    )
    assert {item["status"] for item in simulations} == {
        "BLOCKED_SCORE_BASELINE_UNAVAILABLE"
    }
    assert select_simulated_points(simulations, {}) is None
