from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.services.profile_intelligence_analysis_v2 import (
    ANALYSIS_CONTRACT_VERSION,
    ANALYSIS_SKILL_VERSION,
    canonical_trade_key,
    confusion_matrix,
    deduplicate_rows,
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


def test_model_allowlist_and_default_are_exact_and_no_unknown_fallback():
    assert DEFAULT_AI_MODEL == "claude-haiku-4-5-20251001"
    assert set(SUPPORTED_AI_MODELS) == {
        "claude-fable-5",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
    }
    assert configured_model({"ai_model": "unknown"}) == DEFAULT_AI_MODEL
