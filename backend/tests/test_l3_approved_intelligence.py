import math

from app.ml.intelligence_gate import evaluate_indicator_intelligence_gate
from app.services.ml_challenger_service import (
    MLChallengerService,
    _economic_contract_features,
)


INDICATOR_GATE_CONFIG = {
    "ml_approved_intelligence_min_effective_test_snapshots": 300,
    "ml_approved_intelligence_min_replicated_findings": 2,
    "ml_approved_intelligence_min_distinct_indicators": 2,
    "ml_approved_intelligence_min_prioritize_findings": 1,
    "ml_approved_intelligence_min_block_findings": 1,
}


def test_l3_source_can_be_routed_to_approved_advisory_lane():
    assert MLChallengerService._catboost_lane_for_sources(
        ["L3"], advisory_intelligence=True
    ) == "L3_APPROVED_INTELLIGENCE"
    assert MLChallengerService._catboost_lane_for_sources(
        ["L3"], advisory_intelligence=False
    ) == "L3_PROFILE"


def test_economic_contract_features_distinguish_historical_exit_policies():
    fixed = _economic_contract_features({
        "tp_pct_applied": 1.0,
        "sl_pct_applied": 1.0,
        "barrier_mode": "FIXED",
    }, fee_roundtrip_pct=0.2)
    dynamic = _economic_contract_features({
        "tp_pct_applied": 1.5,
        "sl_pct_applied": 0.6,
        "barrier_mode": "ATR_DYNAMIC",
    }, fee_roundtrip_pct=0.2)

    assert fixed == (1.0, 1.0, 1.0, 0.6, 0.0)
    assert dynamic[0:2] == (1.5, 0.6)
    assert math.isclose(dynamic[2], 2.5)
    assert math.isclose(dynamic[3], 0.8 / 2.1)
    assert dynamic[4] == 1.0


def test_indicator_gate_approves_replicated_advisory_findings_not_predictor_auc():
    result = evaluate_indicator_intelligence_gate({
        "test": {"effective_snapshots": 350, "weighted_roc_auc": 0.49},
        "indicator_intelligence": {"findings": [
            {"indicator": "rsi", "action": "PRIORITIZE"},
            {"indicator": "flow_strength", "action": "BLOCK_CANDIDATE"},
            {"indicator": "adx", "action": "OBSERVE"},
        ]},
    }, INDICATOR_GATE_CONFIG)

    assert result["status"] == "APPROVED"
    assert result["basis"] == "replicated_indicator_findings"
    assert result["execution_authority"] is False


def test_indicator_gate_rejects_findings_without_required_action_coverage():
    result = evaluate_indicator_intelligence_gate({
        "test": {"effective_snapshots": 350},
        "indicator_intelligence": {"findings": [
            {"indicator": "rsi", "action": "PRIORITIZE"},
            {"indicator": "adx", "action": "PRIORITIZE"},
        ]},
    }, INDICATOR_GATE_CONFIG)

    assert result["status"] == "REJECTED"
    assert "block_findings_below_minimum" in result["reasons"]
