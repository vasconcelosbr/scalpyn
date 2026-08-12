from __future__ import annotations

import inspect
import json
from types import SimpleNamespace
import uuid

import pytest

from app.ai_orchestration.contracts import AIRequestIntent
from app.ai_orchestration.langgraph.graphs import build_graph
from app.ai_orchestration.langgraph.registry import resolve_graph
from app.ai_orchestration.langgraph.state import assert_checkpoint_safe
from app.schemas.analysis_chat import (
    AnalysisChatDataMode,
    AnalysisChatOutput,
    AnalysisChatRequestKind,
    AnalysisChatRuntimeConfig,
)
from app.services.analysis_chat_service import AnalysisChatError, AnalysisChatService


def test_chat_does_not_create_new_provider_intent():
    assert {item.value for item in AIRequestIntent} == {
        "NORMAL_ANALYSIS", "FAKE_PROVIDER_CANARY", "REAL_PROVIDER_CANARY",
    }
    assert "FOLLOW_UP_CHAT" in {item.value for item in AnalysisChatRequestKind}


def test_analysis_chat_flags_fail_closed():
    config = AnalysisChatRuntimeConfig()
    assert config.enabled is False
    assert config.readonly_refresh_enabled is False
    assert config.child_analysis_enabled is False
    assert config.proposals_enabled is False
    assert config.governed_actions_enabled is False
    assert config.live_config_write_enabled is False
    assert config.streaming_enabled is False
    assert config.budget_enforcement_enabled is True
    assert config.provider_max_cost_usd == 0
    assert config.request_token_limit == 0


def test_analysis_chat_runtime_config_is_jsonb_serializable():
    payload = AnalysisChatRuntimeConfig(
        enabled=True,
        budget_enforcement_enabled=False,
        provider_max_cost_usd="0.45",
    ).model_dump(mode="json")
    json.dumps(payload)
    assert payload["provider_max_cost_usd"] == "0.45"
    assert payload["budget_enforcement_enabled"] is False


def test_chat_budget_audit_only_mode_disables_every_financial_blocker():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    send_source = inspect.getsource(AnalysisChatService.send_message)
    invoke_source = inspect.getsource(AnalysisChatGraphNodeHandler._invoke_normal_provider)
    assert "config.budget_enforcement_enabled" in send_source
    assert '"AUDIT_ONLY"' in send_source
    assert 'request_json.get("budget_enforcement_enabled") is not False' in invoke_source
    assert invoke_source.count("if budget_enforcement_enabled") >= 3
    assert "AUDIT_ONLY_PROVIDER_RESPONSE_RECEIVED" in invoke_source
    assert "BudgetReservationAudit.activate_placeholder" in invoke_source
    assert "BudgetReservationAudit.reconcile" in invoke_source


@pytest.mark.parametrize("mode", list(AnalysisChatDataMode))
def test_disabled_chat_rejects_every_mode(mode):
    with pytest.raises(AnalysisChatError, match="ANALYSIS_CHAT_DISABLED"):
        AnalysisChatService._require_mode(AnalysisChatRuntimeConfig(), mode)


def test_frozen_mode_is_the_only_default_enabled_mode():
    config = AnalysisChatRuntimeConfig(enabled=True)
    AnalysisChatService._require_mode(config, AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY)
    for mode in (
        AnalysisChatDataMode.ALLOW_READONLY_REFRESH,
        AnalysisChatDataMode.CREATE_CHILD_ANALYSIS,
        AnalysisChatDataMode.DRAFT_PROPOSAL,
    ):
        with pytest.raises(AnalysisChatError, match="MODE_DISABLED"):
            AnalysisChatService._require_mode(config, mode)


def test_request_kind_is_separate_from_data_mode():
    assert AnalysisChatService._request_kind(
        AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY
    ) is AnalysisChatRequestKind.FOLLOW_UP_CHAT
    assert AnalysisChatService._request_kind(
        AnalysisChatDataMode.CREATE_CHILD_ANALYSIS
    ) is AnalysisChatRequestKind.CHILD_ANALYSIS
    assert AnalysisChatService._request_kind(
        AnalysisChatDataMode.DRAFT_PROPOSAL
    ) is AnalysisChatRequestKind.PROPOSAL_DRAFT


def test_staging_fake_intent_is_environment_and_flag_bounded(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "systemic-ai-staging-20260807")
    monkeypatch.setenv("LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "true")
    assert AnalysisChatService._intent() == "FAKE_PROVIDER_CANARY"
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert AnalysisChatService._intent() == "NORMAL_ANALYSIS"


def test_analysis_chat_graph_is_immutable_and_separate():
    graph = resolve_graph("analysis-chat-v1")
    assert graph.semantic_version == "1.1.0"
    assert graph.state_schema_version == "analysis-chat-state-v1.1"
    assert graph.tool_policy_version == "analysis-chat-governed-write-policy-v1"
    assert graph.content_hash == "5eac25a787affe754fa0893a3a92a8eae247754449fb6e29b879ec055692009e"


def test_analysis_chat_graph_compiles_all_human_gates():
    graph = build_graph("analysis-chat-v1")
    rendered = graph.get_graph().draw_mermaid()
    assert "interrupt_child_analysis_confirmation" in rendered
    assert "interrupt_proposal_confirmation" in rendered
    assert "interrupt_proposal_approval" in rendered


def test_chat_thread_mapping_cannot_reuse_parent_thread():
    conversation_id = uuid.uuid4()
    thread_label = f"analysis-chat:{conversation_id}"
    checkpoint_thread = uuid.uuid5(uuid.NAMESPACE_URL, thread_label)
    parent_thread = uuid.uuid4()
    assert thread_label.startswith("analysis-chat:")
    assert checkpoint_thread != parent_thread


def test_checkpoint_contract_rejects_secrets_and_accepts_chat_ids():
    assert_checkpoint_safe({
        "conversation_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "selected_evidence_refs": [{"evidence_id": str(uuid.uuid4())}],
    })
    with pytest.raises(ValueError, match="forbidden key"):
        assert_checkpoint_safe({"conversation_id": "x", "provider_key": "secret"})


def test_chat_output_requires_parent_and_evidence_contract():
    parent_id = uuid.uuid4()
    output = AnalysisChatOutput(
        answer="Resposta limitada ao snapshot.",
        answer_type="EXPLANATION",
        based_on="FROZEN_ANALYSIS",
        parent_analysis_run_id=parent_id,
        evidence_refs=[{"evidence_id": str(uuid.uuid4()), "module": "score_engine"}],
    )
    assert output.parent_analysis_run_id == parent_id
    assert output.new_data_queried is False


def test_provider_parent_id_is_normalized_to_the_canonical_conversation_run():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _normalize_provider_parent,
    )

    canonical_parent = uuid.uuid4()
    provider_output = AnalysisChatOutput(
        answer="Resposta limitada ao snapshot.",
        answer_type="EXPLANATION",
        based_on="FROZEN_ANALYSIS",
        parent_analysis_run_id=uuid.uuid4(),
        evidence_refs=[],
    )

    normalized = _normalize_provider_parent(provider_output, canonical_parent)

    assert normalized.parent_analysis_run_id == canonical_parent
    assert "PROVIDER_PARENT_ANALYSIS_RUN_ID_NORMALIZED" in normalized.warnings


def test_failed_chat_turn_persists_audited_provider_usage():
    from app.tasks.ai_orchestration import _mark_failed

    source = inspect.getsource(_mark_failed)
    assert "AIUsageRecord" in source
    assert "message.tokens_input" in source
    assert "message.tokens_output" in source
    assert "message.cost_usd" in source
    assert "conversation.total_cost_usd" in source


def test_frozen_mode_executes_no_new_tools():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert '["market_regime.get_current"]' in source
    assert "if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL else []" in source


def test_readonly_refresh_has_a_single_none_side_effect_tool_path():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert 'allowlist != ["market_regime.get_current"]' in source
    assert "ModuleToolRuntime" in source
    assert "LIVE_WRITE" not in source
    assert "READONLY_REFRESH_CURRENT_SNAPSHOT_ONLY" in source
    assert '"effective_coverage": "CURRENT_SNAPSHOT_ONLY"' in source
    assert "message.tool_call_ids_json" in source


def test_child_analysis_requires_interrupt_and_does_not_reuse_parent_snapshot():
    graph = resolve_graph("analysis-chat-v1")
    assert "interrupt_child_analysis_confirmation" in graph.node_manifest
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert "CHILD_ANALYSIS_REQUIRES_FRESH_DATASET_AND_BUNDLE" in source
    assert "Nenhuma análise filha foi criada" in source


def test_proposal_is_typed_and_human_gated_twice_before_execution():
    graph = resolve_graph("analysis-chat-v1")
    assert "interrupt_proposal_confirmation" in graph.node_manifest
    assert "interrupt_proposal_approval" in graph.node_manifest
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert "create_governed_change_dry_run" in source
    assert "execute_governed_proposal_if_confirmed" in source
    assert "approve_and_execute_governed_change" in source
    assert "ANALYSIS_CHAT_GOVERNED_CHANGE_ACTOR_MISMATCH" in source
    assert 'decision.get("decision") != "approve"' in source
    assert "if not actor_user_id or str(actor_user_id) != str(request.requested_by_user_id)" in source
    assert 'not in {"approve", "edit"}' not in source
    assert graph.edge_manifest.index(
        ("interrupt_proposal_approval", "execute_governed_proposal_if_confirmed")
    ) > graph.edge_manifest.index(
        ("draft_proposal_if_confirmed", "validate_risk_and_strategy")
    )


def test_each_successful_turn_reconciles_a_budget_reservation():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._persist_answer)
    assert "await self._ensure_reservation" in source
    assert 'reservation.status = "RECONCILED"' in source
    assert "provider_transport_attempted = False" in source


def test_every_accepted_turn_reserves_budget_before_human_interrupts():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert "AIBudgetReservationRecord(" in source
    assert 'status="RESERVED"' in source
    assert "provider_transport_attempted=False" in source


def test_cancel_terminalizes_job_and_releases_reserved_budget():
    source = inspect.getsource(AnalysisChatService.cancel)
    assert 'reservation.status = "RELEASED"' in source
    assert 'job.status = "CANCELLED"' in source
    assert '"CANCELLED_BY_AUTHORIZED_ACTOR"' in source


def test_provider_blocked_is_typed_and_releases_before_transport():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert "ProviderBlockedError" in source
    assert "NORMAL_ANALYSIS_PROVIDER_DISABLED" in source
    assert "_invoke_normal_provider" in source


def test_chat_real_provider_is_per_turn_approved_and_budget_audited():
    from app.ai_orchestration.budget_reservation_audit import BudgetReservationAudit
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    send_source = inspect.getsource(AnalysisChatService.send_message)
    invoke_source = inspect.getsource(AnalysisChatGraphNodeHandler._invoke_normal_provider)
    assert 'scope="ANALYSIS_CHAT_TURN"' in send_source
    assert 'approval_method="ANALYSIS_CHAT_SEND_ACTION"' in send_source
    assert "activate_placeholder" in invoke_source
    assert "mark_transport_started" in invoke_source
    assert "reconcile" in invoke_source
    assert hasattr(BudgetReservationAudit, "activate_placeholder")


def test_provider_decision_context_keeps_rows_and_outputs_without_ledger_duplication():
    from app.services.systemic_langgraph_bridge import _provider_decision_context

    rows = [{"id": "row-1", "score": 42}]
    output = {"quality": "PASS", "freshness": {"age": 1}, "data": rows}
    evidence = SimpleNamespace(
        id=uuid.uuid4(), module_key="shadow_portfolio", tool_name="shadow.test",
        output_json=output, output_hash="hash", quality="PASS",
        freshness_json={"age": 1},
    )
    payload = json.loads(_provider_decision_context({
        "rows": rows,
        "context": {"timeframe": "1h"},
        "context_manifest": {"ledger_only": True},
        "model_approval_id": str(uuid.uuid4()),
    }, [evidence]))
    assert payload["frozen_context"] == {
        "rows": rows, "context": {"timeframe": "1h"},
    }
    assert payload["typed_tool_evidence"][0]["output"] == output
    assert "output_hash" not in payload["typed_tool_evidence"][0]


def test_running_summary_is_versioned_hashed_and_does_not_replace_evidence():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert 'conversation.summary_version = "analysis-chat-summary@1.0.0"' in source
    assert "conversation.summary_hash = _sha(summary)" in source
    assert "selected_evidence_refs" in source


def test_message_idempotency_and_sequence_are_transactional():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert "lock=True" in source
    assert "idempotency_key == idempotency_key" in source
    assert "conversation.message_count" in source


def test_prompt_injection_cannot_expand_tools_or_authority():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert '"PROPOSAL_ONLY"' in source
    assert 'else "ANALYSIS_ONLY"' in source
    assert "if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL" in source
    assert "data_mode=data_mode.value" in source
    assert "tool_allowlist" in source
    assert "normalized" not in source.split('"tool_allowlist":', 1)[1].split("},", 1)[0]


def test_no_order_ml_promotion_or_spot_mutation_path_exists():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler)
    forbidden = ("Order(", "create_order", "promote_model", "spot_engine", "LIVE_WRITE")
    assert not any(value in source for value in forbidden)


def test_run_acquisition_does_not_read_terminal_state_before_execution():
    from app.tasks.ai_orchestration import _acquire_run

    source = inspect.getsource(_acquire_run)
    assert "final_state" not in source
    assert "provider_transport_attempted = None" in source


def test_terminal_message_cannot_regress_to_streaming_in_tail_nodes():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    source = inspect.getsource(AnalysisChatGraphNodeHandler.handle)
    assert 'message.status not in {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}' in source


def test_each_turn_has_a_unique_checkpoint_thread_under_the_conversation():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert 'f"{conversation.thread_id}:message:{user_message.id}"' in source
