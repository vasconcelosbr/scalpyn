from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .config import get_langgraph_settings
from .nodes import INTERRUPT_NODES, NodeHandler, NoopNodeHandler, node_callable
from .registry import GraphDefinition, resolve_graph
from .state import ScalpynGraphState


def build_graph(
    graph_key: str,
    *,
    handler: NodeHandler | None = None,
    checkpointer: Any = None,
    timeout_seconds: int | None = None,
):
    definition: GraphDefinition = resolve_graph(graph_key)
    timeout = timeout_seconds or get_langgraph_settings().node_timeout_seconds
    node_handler = handler or NoopNodeHandler()
    builder = StateGraph(ScalpynGraphState)
    for node_name in definition.node_manifest:
        builder.add_node(
            node_name,
            node_callable(node_name, node_handler, timeout_seconds=timeout),
        )
    builder.add_edge(START, definition.node_manifest[0])
    if graph_key == "analysis-chat-v1":
        _wire_analysis_chat(builder)
        return builder.compile(checkpointer=checkpointer, name=definition.graph_key)
    for source, target in definition.edge_manifest:
        if source in INTERRUPT_NODES:
            def route_after_human_decision(state, *, _target=target):
                decision = (state.get("interrupt_decision") or {}).get("decision")
                return END if decision == "reject" else _target

            builder.add_conditional_edges(source, route_after_human_decision)
        else:
            builder.add_edge(source, target)
    builder.add_edge(definition.node_manifest[-1], END)
    return builder.compile(checkpointer=checkpointer, name=definition.graph_key)


def _wire_analysis_chat(builder: StateGraph) -> None:
    common = (
        ("load_conversation", "authorize_tenant"),
        ("authorize_tenant", "load_parent_analysis"),
        ("load_parent_analysis", "validate_parent_contracts"),
        ("validate_parent_contracts", "load_conversation_memory"),
        ("load_conversation_memory", "classify_followup"),
        ("classify_followup", "select_data_mode"),
    )
    for source, target in common:
        builder.add_edge(source, target)

    def route_mode(state):
        return {
            "FROZEN_ANALYSIS_ONLY": "retrieve_relevant_evidence",
            "ALLOW_READONLY_REFRESH": "plan_readonly_tools",
            "CREATE_CHILD_ANALYSIS": "interrupt_child_analysis_confirmation",
            "DRAFT_PROPOSAL": "interrupt_proposal_confirmation",
        }.get(state.get("data_mode"), "retrieve_relevant_evidence")

    builder.add_conditional_edges("select_data_mode", route_mode)
    builder.add_edge("plan_readonly_tools", "execute_readonly_tools")
    builder.add_edge("execute_readonly_tools", "retrieve_relevant_evidence")
    builder.add_edge("retrieve_relevant_evidence", "decide_if_new_data_required")
    builder.add_edge("decide_if_new_data_required", "assemble_chat_context")
    builder.add_edge("assemble_chat_context", "reserve_budget")
    builder.add_edge("reserve_budget", "invoke_provider")
    builder.add_edge("invoke_provider", "validate_chat_output")

    def route_validated_output(state):
        return (
            "draft_proposal_if_confirmed"
            if state.get("data_mode") == "DRAFT_PROPOSAL"
            else "persist_message_result_usage"
        )

    builder.add_conditional_edges("validate_chat_output", route_validated_output)
    builder.add_edge("persist_message_result_usage", "update_conversation_summary_if_needed")
    builder.add_edge("update_conversation_summary_if_needed", "complete_message")

    def route_after_confirmation(state, target):
        return END if (state.get("interrupt_decision") or {}).get("decision") == "reject" else target

    builder.add_conditional_edges(
        "interrupt_child_analysis_confirmation",
        lambda state: route_after_confirmation(state, "create_child_analysis_if_confirmed"),
    )
    builder.add_edge("create_child_analysis_if_confirmed", "persist_message_result_usage")
    builder.add_conditional_edges(
        "interrupt_proposal_confirmation",
        lambda state: route_after_confirmation(state, "plan_readonly_tools"),
    )

    def route_materialized_proposal(state):
        return (
            "validate_risk_and_strategy"
            if state.get("proposal_id")
            else "persist_message_result_usage"
        )

    builder.add_conditional_edges("draft_proposal_if_confirmed", route_materialized_proposal)
    builder.add_edge("validate_risk_and_strategy", "interrupt_proposal_approval")

    def route_after_explicit_approval(state):
        return (
            "execute_governed_proposal_if_confirmed"
            if (state.get("interrupt_decision") or {}).get("decision") == "approve"
            else END
        )

    builder.add_conditional_edges(
        "interrupt_proposal_approval",
        route_after_explicit_approval,
    )
    builder.add_edge("execute_governed_proposal_if_confirmed", "persist_message_result_usage")
    builder.add_edge("complete_message", END)
