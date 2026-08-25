"""L3 ML is advisory and never changes deterministic authorization."""
from app.tasks.pipeline_scan import _ml_gate_should_block, _ml_gate_audit_payload


# ── _ml_gate_should_block ────────────────────────────────────────────────────

def test_no_eligible_model_skipped_does_not_block():
    assert _ml_gate_should_block(
        {"model_approved": False, "score_status": "SKIPPED",
         "reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE"}
    ) is False


def test_real_model_rejection_is_advisory():
    assert _ml_gate_should_block(
        {"model_approved": False, "score_status": "OK", "win_fast_probability": 0.2}
    ) is False


def test_model_approved_does_not_block():
    assert _ml_gate_should_block(
        {"model_approved": True, "score_status": "OK", "win_fast_probability": 0.9}
    ) is False


def test_infra_exception_is_observed_without_operational_effect():
    assert _ml_gate_should_block(
        {"model_approved": False, "score_status": "ML_EXCEPTION_FAIL_CLOSED"}
    ) is False


def test_empty_result_is_not_applied():
    assert _ml_gate_should_block(None) is False
    assert _ml_gate_should_block({}) is False


# ── _ml_gate_audit_payload: independent advisory state ──────────────────────

def test_audit_payload_passthrough_is_coherent():
    # Sem modelo, decisão passa direto (ALLOW) apesar de model_approved=False.
    p = _ml_gate_audit_payload(
        {"model_approved": False, "score_status": "SKIPPED",
         "reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE"},
        decision_after_ml="ALLOW",
    )
    assert p["gate_action"] is None
    assert p["ml_status"] == "NOT_APPLIED"
    assert p["ml_operational_effect"] is False
    assert p["reason_codes"] == ["NO_ELIGIBLE_MODEL_FOR_LANE"]
    # veredito cru do modelo preservado como telemetria
    assert p["model_approved"] is False
    assert p["score_status"] == "SKIPPED"


def test_audit_payload_real_rejection_is_unfavorable_advice():
    p = _ml_gate_audit_payload(
        {"model_approved": False, "score_status": "OK", "win_fast_probability": 0.2,
         "model_id": "model-1"},
        decision_after_ml="BLOCK",
    )
    assert p["gate_action"] is None
    assert p["ml_status"] == "APPLIED"
    assert p["ml_advisory_decision"] == "UNFAVORABLE"
    assert p["decision_after_ml"] == "ALLOW"
