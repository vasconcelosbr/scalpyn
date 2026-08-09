from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol

from langgraph.types import interrupt

from .state import ScalpynGraphState, assert_checkpoint_safe


class NodeHandler(Protocol):
    async def handle(self, node_name: str, state: ScalpynGraphState) -> dict[str, Any]: ...


class NoopNodeHandler:
    """Compile/test handler. Production supplies a canonical orchestration handler."""

    async def handle(self, node_name: str, state: ScalpynGraphState) -> dict[str, Any]:
        if node_name == "classify_root_cause" and not state.get("root_cause_classification"):
            return {"root_cause_classification": "INSUFFICIENT_EVIDENCE"}
        if node_name == "complete":
            return {"status": "COMPLETED", "terminal_reason": "GRAPH_COMPLETED"}
        return {}


INTERRUPT_NODES = {
    "interrupt_candidate_approval": "CANDIDATE_APPROVAL",
    "interrupt_wait_for_shadow_evidence": "SHADOW_EVIDENCE",
    "interrupt_wait_evidence": "SHADOW_EVIDENCE",
    "interrupt_final_decision": "FINAL_DECISION",
}


def node_callable(
    node_name: str,
    handler: NodeHandler,
    *,
    timeout_seconds: int,
) -> Callable[[ScalpynGraphState], Awaitable[dict[str, Any]]]:
    async def run(state: ScalpynGraphState) -> dict[str, Any]:
        if node_name in (state.get("completed_nodes") or []):
            return {}
        assert_checkpoint_safe(dict(state))
        if node_name in INTERRUPT_NODES:
            decision = interrupt({
                "interrupt_type": INTERRUPT_NODES[node_name],
                "graph_run_id": state.get("graph_run_id"),
                "authority": state.get("authority"),
                "allowed_decisions": ["approve", "reject", "edit"],
            })
            if not isinstance(decision, dict):
                raise ValueError("interrupt decision must be an object")
            updates: dict[str, Any] = {"interrupt_decision": decision, "pending_interrupt": None}
        else:
            async with asyncio.timeout(timeout_seconds):
                updates = await handler.handle(node_name, state)
        if not isinstance(updates, dict):
            raise TypeError(f"node handler returned non-object for {node_name}")
        updates.update({
            "current_node": node_name,
            "completed_nodes": [node_name],
            "event_keys": [f"{state.get('graph_run_id')}:{node_name}:completed"],
        })
        assert_checkpoint_safe(updates)
        return updates

    run.__name__ = node_name
    return run
