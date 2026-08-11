from __future__ import annotations

from types import SimpleNamespace

from app.api.ai_graphs import _result_payload


def test_result_payload_exposes_persisted_analysis_and_user_facing_sections():
    result = SimpleNamespace(
        status="COMPLETED",
        result_json={
            "tenant_id": "must-not-leak-from-result-document",
            "provider": "internal-provider",
            "analysis": {
                "diagnosis": "Weak entry confirmation.",
                "root_cause_classification": "ENTRY_SIGNAL_WEAKNESS",
                "affected_modules": ["strategy_profiles", "score_engine"],
                "evidence": [{
                    "tool": "compare_strategy_policy",
                    "finding": "Momentum confirmation is insufficient.",
                    "evidence_id": "evidence-1",
                }],
                "memory_hits": [{"id": "memory-1"}],
            },
            "recommendations": [{"target_path": "entry.rsi_max"}],
            "warnings": ["Historical sample is bounded."],
            "limitations": ["No live write authority."],
        },
    )

    payload = _result_payload(result)

    assert payload["status"] == "COMPLETED"
    assert payload["analysis"]["root_cause_classification"] == "ENTRY_SIGNAL_WEAKNESS"
    assert payload["recommendations"] == [{"target_path": "entry.rsi_max"}]
    assert payload["warnings"] == ["Historical sample is bounded."]
    assert payload["limitations"] == ["No live write authority."]
    assert payload["memory_hits"] == [{"id": "memory-1"}]
    assert "tenant_id" not in payload
    assert "provider" not in payload


def test_result_payload_is_stable_for_missing_or_malformed_legacy_result():
    assert _result_payload(None) == {
        "status": None,
        "analysis": None,
        "recommendations": [],
        "warnings": [],
        "limitations": [],
        "memory_hits": [],
    }

    malformed = SimpleNamespace(status="COMPLETED", result_json={
        "analysis": "not-an-object",
        "recommendations": {"unexpected": True},
        "warnings": None,
        "limitations": "not-a-list",
        "memory_hits": 42,
    })
    assert _result_payload(malformed) == {
        "status": "COMPLETED",
        "analysis": None,
        "recommendations": [],
        "warnings": [],
        "limitations": [],
        "memory_hits": [],
    }
