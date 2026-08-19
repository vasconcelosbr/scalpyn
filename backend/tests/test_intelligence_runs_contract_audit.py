"""Read-only audit assertions for the deployed Intelligence Runs contract.

These tests intentionally encode the desired contract without changing runtime
behavior.  A failure is an audit finding, not permission to repair production.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ai_orchestration.contracts import AIRequestIntent
from app.ai_orchestration.errors import (
    GraphNodeExecutionError,
    ProviderBlockedError,
    ProviderOutputError,
)
from app.ai_orchestration.request_intent import validate_provider_intent_gate
from app.tasks.ai_orchestration import _failure_details


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = (
    ROOT
    / "intelligence_audit_evidence"
    / "db"
    / "production_intelligence_runs_audit.jsonl"
)


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _evidence(evidence_id: str) -> dict:
    if not EVIDENCE.exists():
        pytest.skip("requires the separately retained production audit evidence bundle")
    for line in EVIDENCE.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if record["evidence_id"] == evidence_id:
            return record
    raise AssertionError(f"missing audit evidence: {evidence_id}")


def test_normal_analysis_is_not_blocked_by_canary_only_flag():
    validate_provider_intent_gate(
        AIRequestIntent.NORMAL_ANALYSIS,
        environment_name="production",
        fake_provider_canary_enabled=False,
        real_provider_canary_enabled=False,
        normal_analysis_provider_enabled=True,
    )


def test_canary_flag_only_applies_to_canary_requests():
    with pytest.raises(ProviderBlockedError) as caught:
        validate_provider_intent_gate(
            AIRequestIntent.REAL_PROVIDER_CANARY,
            environment_name="staging",
            fake_provider_canary_enabled=False,
            real_provider_canary_enabled=False,
            normal_analysis_provider_enabled=True,
        )
    assert caught.value.reason_code == "REAL_PROVIDER_CANARY_DISABLED"


def test_provider_disabled_returns_typed_blocked_status():
    failure = _failure_details(GraphNodeExecutionError(
        "invoke_provider",
        ProviderBlockedError("NORMAL_PROVIDER_DISABLED", "normal provider disabled"),
    ))
    assert failure == {
        "failed_node": "invoke_provider",
        "error_kind": "PROVIDER_BLOCKED",
        "reason_code": "NORMAL_PROVIDER_DISABLED",
        "safe_message": "normal provider disabled",
        "provider_transport_attempted": False,
        "terminal_reason": "PROVIDER_BLOCKED",
        "diagnostics": None,
    }


def test_provider_output_failure_preserves_attempted_transport():
    failure = _failure_details(GraphNodeExecutionError(
        "validate_output",
        ProviderOutputError("PROVIDER_OUTPUT_TRUNCATED"),
    ))
    assert failure == {
        "failed_node": "validate_output",
        "error_kind": "PROVIDER_OUTPUT_FAILED",
        "reason_code": "PROVIDER_OUTPUT_TRUNCATED",
        "safe_message": "Provider returned an incomplete or invalid structured response",
        "provider_transport_attempted": True,
        "terminal_reason": "FAIL_CLOSED",
        "diagnostics": None,
    }


def test_provider_output_failure_carries_diagnostics_when_present():
    """AUD-IR-CTR-001 (4.3/L14): stop_reason/schema-path metadata that used
    to be discarded before persistence must now survive onto _mark_failed's
    input, without ever including a raw prompt or provider response body."""
    failure = _failure_details(GraphNodeExecutionError(
        "validate_output",
        ProviderOutputError(
            "PROVIDER_OUTPUT_TRUNCATED",
            {"provider_stop_reason": "max_tokens", "provider_response_ref": "req_abc123"},
        ),
    ))
    assert failure["diagnostics"] == {
        "provider_stop_reason": "max_tokens",
        "provider_response_ref": "req_abc123",
    }


def test_bridge_marks_post_transport_output_failures_as_attempted():
    bridge = _source("backend/app/services/systemic_langgraph_bridge.py")
    terminal_branch = bridge.split("if response.terminal_error_code is not None:", 1)[1]
    assert '"provider_transport_attempted": True' in terminal_branch.split("try:", 1)[0]


def test_assemble_evidence_persists_manifest_before_provider_gate():
    registry = _source("backend/app/ai_orchestration/langgraph/registry.py")
    assert registry.index('"assemble_evidence"') < registry.index('"invoke_provider"')


def test_ui_displays_provider_block_not_generic_failure():
    page = _source("frontend/app/intelligence-runs/page.tsx")
    assert "PROVIDER_BLOCKED" in page or "BLOCKED_PROVIDER" in page


def test_configured_model_equals_effective_model():
    rows = _evidence("E-DB-012")["rows"]
    assert rows and all(row["configured_equals_effective"] for row in rows)


def test_missing_budget_blocks_before_provider():
    bridge = _source("backend/app/services/systemic_langgraph_bridge.py")
    provider_transport = bridge.index("await SystemicLangGraphBridge.execute_json_provider")
    assert bridge.index("BOUNDED_AI_BUDGET_POLICY_REQUIRED") < provider_transport


def test_all_module_tools_link_to_evidence():
    rows = _evidence("E-DB-023")["rows"]
    assert rows and all(
        row["calls_without_evidence"] == 0
        and row["audit_calls"] == row["evidence_rows"]
        for row in rows
    )


def test_dataset_and_bundle_survive_provider_block():
    rows = _evidence("E-DB-010")["rows"]
    assert rows and all(
        row["dataset_present"]
        and row["bundle_present"]
        and row["dataset_tenant_match"]
        and row["bundle_tenant_match"]
        for row in rows
    )


def test_failed_run_terminalizes_job_consistently():
    rows = _evidence("E-DB-011")["rows"]
    assert rows and all(
        row["graph_status"] == "FAILED"
        and row["job_status"] == "FAILED_TERMINAL"
        and row["graph_error"] == row["job_error"]
        and row["request_match"]
        and row["tenant_match"]
        for row in rows
    )


def test_no_cross_tenant_links():
    rows = _evidence("E-DB-025")["rows"]
    assert rows and all(row["mismatches"] == 0 for row in rows)
