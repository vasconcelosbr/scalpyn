from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
import uuid


GRAPH_NAMESPACE = uuid.UUID("a42c5ab1-1bda-5e45-ae66-554315834a7d")
STATE_SCHEMA_VERSION = "scalpyn-graph-state-v1"
TOOL_POLICY_VERSION = "systemic-tool-policy-v1"
STATE_SCHEMA_VERSION_V2 = "scalpyn-graph-state-v2"
TOOL_POLICY_VERSION_V2 = "systemic-multimodule-tool-policy-v1"

SYSTEMIC_NODES = (
    "load_request", "authorize_tenant", "resolve_provider_model", "resolve_prompt",
    "freeze_canonical_dataset", "resolve_configuration_bundle", "run_data_quality_gate",
    "load_related_module_context", "retrieve_decision_memory", "plan_typed_tools",
    "execute_readonly_tools", "assemble_evidence", "invoke_provider",
    "validate_structured_output", "persist_result_usage_audit", "complete",
)
ROOT_CAUSE_NODES = (
    "load_request", "authorize_tenant", "identify_change_window", "load_before_after_versions",
    "validate_comparability", "compare_market_regime", "compare_data_quality",
    "compare_symbol_profile_concentration", "compare_exit_policy",
    "compare_model_feature_contract", "run_paired_replay_when_available",
    "classify_root_cause", "generate_evidence_bound_diagnosis", "persist_result_usage_audit",
    "complete",
)
REGENERATIVE_NODES = (
    "detect_or_receive_degradation", "validate_dataset_and_bundle", "classify_root_cause",
    "create_hypothesis", "retrieve_similar_decision_memory", "check_do_not_repeat_context",
    "design_ablation_candidates", "interrupt_candidate_approval",
    "create_immutable_candidate_versions", "start_shadow_experiment",
    "interrupt_wait_for_shadow_evidence", "resume_from_shadow_event",
    "evaluate_champion_challenger", "propose_keep_reject_or_rollback",
    "interrupt_final_decision", "apply_shadow_only_pointer_or_create_rollback_version",
    "persist_experiment_outcome", "persist_decision_memory", "complete",
)
COPILOT_NODES = (
    "load_request", "authorize_tenant", "resolve_provider_model", "resolve_prompt",
    "load_related_module_context", "retrieve_decision_memory", "plan_typed_tools",
    "execute_readonly_tools", "assemble_evidence", "invoke_provider",
    "validate_structured_output", "persist_result_usage_audit", "complete",
)

SYSTEMIC_V2_NODES = (
    "load_request", "authorize_tenant", "resolve_origin_module",
    "resolve_module_dependency_plan", "resolve_provider_model", "resolve_prompt",
    "freeze_dataset", "resolve_configuration_bundle", "run_data_quality_gate",
    "load_strategy_profiles", "load_shadow", "load_score_engine", "load_global_risk",
    "load_strategies", "load_ml_evidence", "load_social_score", "load_market_regime",
    "load_audit_memory", "run_module_conflict_checks", "plan_tools", "execute_tools",
    "assemble_evidence", "invoke_provider", "validate_output", "persist_result", "complete",
)
ROOT_CAUSE_V2_NODES = (
    "load_request", "authorize_tenant", "identify_change_set", "load_before_after",
    "validate_contract_equivalence", "compare_data_quality", "compare_market_regime",
    "compare_social_context", "compare_profile_version", "compare_score_version",
    "compare_risk_policy", "compare_strategy_policy", "compare_ml_authority",
    "run_paired_replay", "classify_root_cause", "assemble_evidence", "invoke_provider",
    "validate_output", "persist_result", "complete",
)
REGENERATIVE_V2_NODES = (
    "receive_degradation", "freeze_comparable_dataset", "resolve_bundle",
    "classify_root_cause", "create_hypothesis", "retrieve_contextual_memory",
    "block_repeated_failed_path", "design_ablation", "validate_risk_and_strategy",
    "interrupt_candidate_approval", "create_profile_candidate_version",
    "create_score_candidate_version", "start_shadow_experiment", "interrupt_wait_evidence",
    "resume_from_shadow_event", "evaluate_deterministically", "interrupt_final_decision",
    "keep_reject_or_create_rollback_version", "persist_outcome",
    "persist_contextual_memory", "complete",
)
COPILOT_V2_NODES = (
    "load_request", "authorize_tenant", "resolve_origin_module",
    "resolve_module_dependency_plan", "resolve_provider_model", "resolve_prompt",
    "load_strategy_profiles", "load_shadow", "load_score_engine", "load_global_risk",
    "load_strategies", "load_ml_evidence", "load_social_score", "load_market_regime",
    "load_audit_memory", "plan_tools", "execute_tools", "assemble_evidence",
    "invoke_provider", "validate_output", "persist_result", "complete",
)

ANALYSIS_CHAT_NODES = (
    "load_conversation", "authorize_tenant", "load_parent_analysis",
    "validate_parent_contracts", "load_conversation_memory", "classify_followup",
    "select_data_mode", "retrieve_relevant_evidence", "decide_if_new_data_required",
    "plan_readonly_tools", "execute_readonly_tools", "interrupt_child_analysis_confirmation",
    "create_child_analysis_if_confirmed", "interrupt_proposal_confirmation",
    "draft_proposal_if_confirmed", "validate_risk_and_strategy",
    "interrupt_proposal_approval", "execute_governed_proposal_if_confirmed",
    "assemble_chat_context", "reserve_budget",
    "invoke_provider", "validate_chat_output", "persist_message_result_usage",
    "update_conversation_summary_if_needed", "complete_message",
)
ANALYSIS_CHAT_EDGES = (
    ("load_conversation", "authorize_tenant"),
    ("authorize_tenant", "load_parent_analysis"),
    ("load_parent_analysis", "validate_parent_contracts"),
    ("validate_parent_contracts", "load_conversation_memory"),
    ("load_conversation_memory", "classify_followup"),
    ("classify_followup", "select_data_mode"),
    ("retrieve_relevant_evidence", "decide_if_new_data_required"),
    ("decide_if_new_data_required", "assemble_chat_context"),
    ("plan_readonly_tools", "execute_readonly_tools"),
    ("execute_readonly_tools", "retrieve_relevant_evidence"),
    ("interrupt_child_analysis_confirmation", "create_child_analysis_if_confirmed"),
    ("create_child_analysis_if_confirmed", "persist_message_result_usage"),
    ("interrupt_proposal_confirmation", "plan_readonly_tools"),
    ("validate_chat_output", "draft_proposal_if_confirmed"),
    ("draft_proposal_if_confirmed", "validate_risk_and_strategy"),
    ("validate_risk_and_strategy", "interrupt_proposal_approval"),
    ("interrupt_proposal_approval", "execute_governed_proposal_if_confirmed"),
    ("execute_governed_proposal_if_confirmed", "persist_message_result_usage"),
    ("assemble_chat_context", "reserve_budget"),
    ("reserve_budget", "invoke_provider"),
    ("invoke_provider", "validate_chat_output"),
    ("validate_chat_output", "persist_message_result_usage"),
    ("persist_message_result_usage", "update_conversation_summary_if_needed"),
    ("update_conversation_summary_if_needed", "complete_message"),
)


@dataclass(frozen=True)
class GraphDefinition:
    id: uuid.UUID
    graph_key: str
    semantic_version: str
    state_schema_version: str
    status: str
    content_hash: str
    code_revision: str
    node_manifest: tuple[str, ...]
    edge_manifest: tuple[tuple[str, str], ...]
    tool_policy_version: str
    created_at: datetime
    approved_at: datetime

    def mermaid(self) -> str:
        lines = ["flowchart TD", f'    START(["START"]) --> {self.node_manifest[0]}']
        lines.extend(f"    {source} --> {target}" for source, target in self.edge_manifest)
        lines.append(f'    {self.node_manifest[-1]} --> END(["END"])')
        return "\n".join(lines) + "\n"


def _definition(graph_key: str, nodes: tuple[str, ...]) -> GraphDefinition:
    is_v2 = graph_key.endswith("-v2")
    version = "2.0.0" if is_v2 else "1.0.0"
    state_schema_version = STATE_SCHEMA_VERSION_V2 if is_v2 else STATE_SCHEMA_VERSION
    tool_policy_version = TOOL_POLICY_VERSION_V2 if is_v2 else TOOL_POLICY_VERSION
    edges = tuple((nodes[index], nodes[index + 1]) for index in range(len(nodes) - 1))
    payload = {
        "graph_key": graph_key,
        "semantic_version": version,
        "state_schema_version": state_schema_version,
        "node_manifest": list(nodes),
        "edge_manifest": [list(edge) for edge in edges],
        "tool_policy_version": tool_policy_version,
    }
    content_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    approved_at = datetime(2026, 8, 8 if is_v2 else 7, tzinfo=timezone.utc)
    return GraphDefinition(
        id=uuid.uuid5(GRAPH_NAMESPACE, f"{graph_key}@{version}"),
        graph_key=graph_key,
        semantic_version=version,
        state_schema_version=state_schema_version,
        status="APPROVED",
        content_hash=content_hash,
        code_revision="149_systemic_multimodule_langgraph" if is_v2 else "148_langgraph_runtime",
        node_manifest=nodes,
        edge_manifest=edges,
        tool_policy_version=tool_policy_version,
        created_at=approved_at,
        approved_at=approved_at,
    )


def _analysis_chat_definition() -> GraphDefinition:
    payload = {
        "graph_key": "analysis-chat-v1",
        "semantic_version": "1.3.0",
        "state_schema_version": "analysis-chat-state-v1.1",
        "node_manifest": list(ANALYSIS_CHAT_NODES),
        "edge_manifest": [list(edge) for edge in ANALYSIS_CHAT_EDGES],
        "tool_policy_version": "analysis-chat-governed-write-policy-v3",
    }
    approved_at = datetime(2026, 8, 20, 19, tzinfo=timezone.utc)
    return GraphDefinition(
        id=uuid.uuid5(GRAPH_NAMESPACE, "analysis-chat-v1@1.3.0"),
        graph_key=payload["graph_key"],
        semantic_version=payload["semantic_version"],
        state_schema_version=payload["state_schema_version"],
        status="APPROVED",
        content_hash=hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        code_revision="191_chat_explicit_proposal",
        node_manifest=ANALYSIS_CHAT_NODES,
        edge_manifest=ANALYSIS_CHAT_EDGES,
        tool_policy_version=payload["tool_policy_version"],
        created_at=approved_at,
        approved_at=approved_at,
    )


_REGISTRY = {
    definition.graph_key: definition
    for definition in (
        _definition("systemic-analysis-v1", SYSTEMIC_NODES),
        _definition("root-cause-audit-v1", ROOT_CAUSE_NODES),
        _definition("regenerative-shadow-v1", REGENERATIVE_NODES),
        _definition("copilot-systemic-v1", COPILOT_NODES),
        _definition("systemic-analysis-v2", SYSTEMIC_V2_NODES),
        _definition("root-cause-audit-v2", ROOT_CAUSE_V2_NODES),
        _definition("regenerative-shadow-v2", REGENERATIVE_V2_NODES),
        _definition("copilot-systemic-v2", COPILOT_V2_NODES),
        _analysis_chat_definition(),
    )
}
graph_registry = MappingProxyType(_REGISTRY)


def resolve_graph(graph_key: str) -> GraphDefinition:
    try:
        definition = graph_registry[graph_key]
    except KeyError as exc:
        raise LookupError(f"unknown graph: {graph_key}") from exc
    if definition.status != "APPROVED":
        raise RuntimeError(f"graph is not approved: {graph_key}")
    return definition
