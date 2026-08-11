from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.ai_orchestration.budget_service import BudgetReservation, BudgetService
from app.ai_orchestration.contracts import AIRequestIntent, Authority
from app.ai_orchestration.errors import GraphNodeExecutionError, ProviderBlockedError
from app.ai_orchestration.request_intent import resolve_request_intent, validate_provider_intent_gate
from app.ai_orchestration.langgraph.checkpoint import checkpoint_connection_string
from app.schemas.ai_provider_runtime_config import AIProviderRuntimeConfig
from app.tasks.ai_orchestration import _failure_details


ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_legacy_request_without_markers_resolves_normal_analysis():
    assert resolve_request_intent({}) is AIRequestIntent.NORMAL_ANALYSIS


def test_legacy_fake_markers_preserve_fake_canary_semantics():
    assert resolve_request_intent({
        "staging_canary": True,
        "fake_provider": True,
    }) is AIRequestIntent.FAKE_PROVIDER_CANARY


@pytest.mark.parametrize("marker", ["staging_canary", "fake_provider"])
def test_incomplete_legacy_fake_markers_fail_closed(marker):
    with pytest.raises(ProviderBlockedError) as caught:
        resolve_request_intent({marker: True})
    assert caught.value.reason_code == "REQUEST_INTENT_MARKERS_AMBIGUOUS"
    assert caught.value.provider_transport_attempted is False


def test_ambiguous_markers_fail_closed():
    with pytest.raises(ProviderBlockedError) as caught:
        resolve_request_intent({
            "request_intent": "NORMAL_ANALYSIS",
            "staging_canary": True,
            "fake_provider": True,
        })
    assert caught.value.reason_code == "REQUEST_INTENT_MARKERS_AMBIGUOUS"
    assert caught.value.provider_transport_attempted is False


def test_real_canary_requires_separate_server_authorization():
    with pytest.raises(ProviderBlockedError) as caught:
        resolve_request_intent({
            "request_intent": "REAL_PROVIDER_CANARY",
            "provider_canary": True,
        })
    assert caught.value.reason_code == "REAL_PROVIDER_CANARY_AUTHORIZATION_REQUIRED"


def test_real_canary_off_blocks_before_adapter_call():
    adapter = AsyncMock()
    with pytest.raises(ProviderBlockedError) as caught:
        validate_provider_intent_gate(
            AIRequestIntent.REAL_PROVIDER_CANARY,
            environment_name="staging",
            fake_provider_canary_enabled=False,
            real_provider_canary_enabled=False,
            normal_analysis_provider_enabled=True,
        )
        adapter()
    assert caught.value.reason_code == "REAL_PROVIDER_CANARY_DISABLED"
    adapter.assert_not_called()


def test_fake_canary_requires_staging_and_fake_flag():
    for environment, enabled, reason in (
        ("production", True, "FAKE_PROVIDER_CANARY_STAGING_ONLY"),
        ("systemic-ai-staging", False, "FAKE_PROVIDER_CANARY_DISABLED"),
    ):
        with pytest.raises(ProviderBlockedError) as caught:
            validate_provider_intent_gate(
                AIRequestIntent.FAKE_PROVIDER_CANARY,
                environment_name=environment,
                fake_provider_canary_enabled=enabled,
                real_provider_canary_enabled=False,
                normal_analysis_provider_enabled=False,
            )
        assert caught.value.reason_code == reason


def test_normal_operational_gate_is_independent_from_canary_flags():
    validate_provider_intent_gate(
        AIRequestIntent.NORMAL_ANALYSIS,
        environment_name="production",
        fake_provider_canary_enabled=False,
        real_provider_canary_enabled=False,
        normal_analysis_provider_enabled=True,
    )
    with pytest.raises(ProviderBlockedError) as caught:
        validate_provider_intent_gate(
            AIRequestIntent.NORMAL_ANALYSIS,
            environment_name="production",
            fake_provider_canary_enabled=True,
            real_provider_canary_enabled=True,
            normal_analysis_provider_enabled=False,
        )
    assert caught.value.reason_code == "NORMAL_PROVIDER_DISABLED"


def test_normal_gate_config_has_no_canary_authority_and_defaults_closed():
    assert AIProviderRuntimeConfig().normal_analysis_provider_enabled is False
    with pytest.raises(ValidationError):
        AIProviderRuntimeConfig.model_validate({"real_provider_canary_enabled": True})


def test_failed_node_is_preserved_outside_node_transaction():
    failure = _failure_details(GraphNodeExecutionError(
        "invoke_provider",
        ProviderBlockedError("NORMAL_PROVIDER_DISABLED", "normal provider disabled"),
    ))
    assert failure["failed_node"] == "invoke_provider"
    assert failure["error_kind"] == "PROVIDER_BLOCKED"
    assert failure["provider_transport_attempted"] is False
    task_source = _source("backend/app/tasks/ai_orchestration.py")
    assert "node_name=failed_node" in task_source
    assert '"correlation_id": correlation_id' in task_source


def test_handler_persists_last_completed_node_separately():
    handler = _source("backend/app/ai_orchestration/langgraph/handler.py")
    assert "run.last_completed_node = node_name" in handler
    assert "GraphNodeExecutionError(node_name, exc)" in handler


def test_budget_reconciliation_is_deterministic_and_releases_unused_tokens():
    reservation = BudgetReservation(
        id=uuid4(), estimated_tokens=100, estimated_cost=Decimal("0.01"), remaining_tokens=900,
    )
    first = BudgetService.reconcile(
        reservation, actual_input=30, actual_output=20, actual_cost=Decimal("0.004"),
    )
    second = BudgetService.reconcile(
        reservation, actual_input=30, actual_output=20, actual_cost=Decimal("0.004"),
    )
    assert first == second
    assert first["released_tokens"] == 50
    assert first["overage_tokens"] == 0


def test_budget_reservation_is_persistent_idempotent_and_tenant_scoped():
    model = _source("backend/app/models/systemic_ai.py")
    service = _source("backend/app/ai_orchestration/budget_reservation_audit.py")
    migration = _source("backend/alembic/versions/156_intelligence_run_intents.py")
    assert 'UniqueConstraint("ai_request_id", name="uq_ai_budget_reservation_request")' in model
    assert "on_conflict_do_nothing" in service
    assert "BUDGET_RESERVATION_TENANT_MISMATCH" in service
    assert 'status="RELEASED"' in service
    assert 'revision = "156_intelligence_run_intents"' in migration
    assert 'down_revision = "155_ai_analysis_profiles"' in migration


def test_bridge_resolves_intent_before_runtime_gate():
    bridge = _source("backend/app/services/systemic_langgraph_bridge.py")
    prepared_request = bridge.split("async def execute_prepared_request", 1)[1]
    assert prepared_request.index("resolve_request_intent(request_json)") < prepared_request.index(
        "settings.require_runtime()"
    )


def test_fake_canary_never_reaches_real_key_path():
    bridge = _source("backend/app/services/systemic_langgraph_bridge.py")
    fake_branch = bridge.index("if request_intent is AIRequestIntent.FAKE_PROVIDER_CANARY")
    fake_return = bridge.index("return result_json", fake_branch)
    real_key = bridge.index("select(AIProviderKey)")
    assert fake_branch < fake_return < real_key
    assert 'provider="fake"' in bridge[fake_branch:fake_return]


def test_budget_reservation_precedes_transport_and_results_follow_validation():
    bridge = _source("backend/app/services/systemic_langgraph_bridge.py")
    reserve = bridge.index("BudgetReservationAudit.reserve")
    transport = bridge.index("await SystemicLangGraphBridge.execute_json_provider")
    validation = bridge.index("validate(response.output", transport)
    result = bridge.index("AIResultRecord(", validation)
    assert reserve < transport < validation < result


def test_authority_remains_analysis_or_shadow_only():
    assert Authority.ANALYSIS_ONLY.value == "ANALYSIS_ONLY"
    assert Authority.SHADOW_ONLY.value == "SHADOW_ONLY"
    staging = _source("backend/app/ai_orchestration/langgraph/staging_canary.py")
    assert '"ANALYSIS_ONLY"' in staging
    assert '"SHADOW_ONLY"' in staging
    assert '"live_write": False' in staging


def test_ui_distinguishes_provider_block_and_nodes():
    page = _source("frontend/app/intelligence-runs/page.tsx")
    assert "Provider bloqueado" in page
    assert "last_completed_node" in page
    assert "failed_node" in page
    assert "o canario real nao corrige uma analise normal" in page


def test_checkpoint_normalizes_asyncpg_ssl_for_psycopg(monkeypatch):
    monkeypatch.setenv("AI_ORCHESTRATION_RUNTIME", "langgraph")
    monkeypatch.setenv("LANGGRAPH_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("LANGGRAPH_STRICT_MSGPACK", "true")
    value = checkpoint_connection_string(
        "postgresql+asyncpg://user:password@example.test:5432/db?ssl=require",
    )
    assert "sslmode=require" in value
    assert "ssl=require" not in value
