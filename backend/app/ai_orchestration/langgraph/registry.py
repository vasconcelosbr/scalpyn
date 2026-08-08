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
    version = "1.0.0"
    edges = tuple((nodes[index], nodes[index + 1]) for index in range(len(nodes) - 1))
    payload = {
        "graph_key": graph_key,
        "semantic_version": version,
        "state_schema_version": STATE_SCHEMA_VERSION,
        "node_manifest": list(nodes),
        "edge_manifest": [list(edge) for edge in edges],
        "tool_policy_version": TOOL_POLICY_VERSION,
    }
    content_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    approved_at = datetime(2026, 8, 7, tzinfo=timezone.utc)
    return GraphDefinition(
        id=uuid.uuid5(GRAPH_NAMESPACE, f"{graph_key}@{version}"),
        graph_key=graph_key,
        semantic_version=version,
        state_schema_version=STATE_SCHEMA_VERSION,
        status="APPROVED",
        content_hash=content_hash,
        code_revision="148_langgraph_runtime",
        node_manifest=nodes,
        edge_manifest=edges,
        tool_policy_version=TOOL_POLICY_VERSION,
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
