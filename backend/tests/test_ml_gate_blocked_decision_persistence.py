from pathlib import Path


def _pipeline_source() -> str:
    return (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "app"
        / "tasks"
        / "pipeline_scan.py"
    ).read_text(encoding="utf-8")


def test_ml_gate_blocked_decision_forces_decision_log_persistence():
    source = _pipeline_source()
    idx = source.index("if _ml_gate_enabled and sym in _ml_gate_scores")
    snippet = source[idx: idx + 500]

    assert "should_log = True" in snippet
    assert 'event_type = "ML_GATE_ALLOWED" if d.get("decision") == "ALLOW" else "ML_GATE_BLOCKED"' in snippet
    assert "decisions_to_log.append(d)" in source


def test_ml_gate_blocked_decision_sets_first_class_lineage_fields():
    source = _pipeline_source()
    idx = source.index('_d["ranking_id"] = _ml_gate_scores')
    snippet = source[idx: idx + 1800]

    for field in (
        '["ranking_id"]',
        '["model_id"]',
        '["model_version"]',
        '["model_lane"]',
        '["probability"]',
        '["threshold_used"]',
        '["score_status"]',
        '["gate_action"]',
        '["reason_codes"]',
        '["orchestrator_payload"]',
        '["ml_gate_enabled"]',
    ):
        assert field in snippet


def test_ranking_decision_id_is_updated_after_decision_flush():
    source = _pipeline_source()
    idx = source.index("decision_payloads = await _persist_decision_logs")
    snippet = source[idx: idx + 1400]

    assert "UPDATE ml_opportunity_rankings" in snippet
    assert "SET decision_id = :decision_id" in snippet
    assert "AND decision_id IS NULL" in snippet

