"""P2 (Fase 1.6) — o ML gate NÃO bloqueia sinal real quando não há modelo.

Sem modelo elegível (score_status=SKIPPED) o gate deixa o L3 ALLOW passar
(contrato do prediction_service). Bloqueia apenas quando um modelo active
rejeita de fato (OK + not approved) ou em falha de infra com modelo presente
(ML_EXCEPTION_FAIL_CLOSED).
"""
from app.tasks.pipeline_scan import _ml_gate_should_block, _ml_gate_audit_payload


# ── _ml_gate_should_block ────────────────────────────────────────────────────

def test_no_eligible_model_skipped_does_not_block():
    assert _ml_gate_should_block(
        {"model_approved": False, "score_status": "SKIPPED",
         "reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE"}
    ) is False


def test_real_model_rejection_blocks():
    assert _ml_gate_should_block(
        {"model_approved": False, "score_status": "OK", "win_fast_probability": 0.2}
    ) is True


def test_model_approved_does_not_block():
    assert _ml_gate_should_block(
        {"model_approved": True, "score_status": "OK", "win_fast_probability": 0.9}
    ) is False


def test_infra_exception_stays_fail_closed():
    assert _ml_gate_should_block(
        {"model_approved": False, "score_status": "ML_EXCEPTION_FAIL_CLOSED"}
    ) is True


def test_empty_result_is_conservative_block():
    # Sem informação nenhuma → não é SKIPPED → fail-closed (bloqueia).
    assert _ml_gate_should_block(None) is True
    assert _ml_gate_should_block({}) is True


# ── _ml_gate_audit_payload: gate_action reflete a ação efetiva ───────────────

def test_audit_payload_passthrough_is_coherent():
    # Sem modelo, decisão passa direto (ALLOW) apesar de model_approved=False.
    p = _ml_gate_audit_payload(
        {"model_approved": False, "score_status": "SKIPPED",
         "reason_code": "NO_ELIGIBLE_MODEL_FOR_LANE"},
        decision_after_ml="ALLOW",
    )
    assert p["gate_action"] == "ALLOW"
    assert p["ml_gate"] == "ALLOW"
    assert "ML_GATE_ALLOWED" in p["reason_codes"]
    assert "ML_GATE_BLOCKED" not in p["reason_codes"]
    # veredito cru do modelo preservado como telemetria
    assert p["model_approved"] is False
    assert p["score_status"] == "SKIPPED"


def test_audit_payload_real_rejection_is_block():
    p = _ml_gate_audit_payload(
        {"model_approved": False, "score_status": "OK", "win_fast_probability": 0.2},
        decision_after_ml="BLOCK",
    )
    assert p["gate_action"] == "BLOCK"
    assert "ML_GATE_BLOCKED" in p["reason_codes"]
