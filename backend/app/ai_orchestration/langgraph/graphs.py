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
