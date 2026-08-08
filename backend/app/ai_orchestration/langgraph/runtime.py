from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol
from uuid import UUID

from ..contracts import AIRequest, AIResult
from .config import get_langgraph_settings
from .graphs import build_graph
from .nodes import NodeHandler
from .registry import resolve_graph
from .state import ScalpynGraphState, assert_checkpoint_safe


class OrchestrationRuntime(Protocol):
    async def execute(self, request: AIRequest) -> AIResult: ...


class NativePythonOrchestrationRuntime:
    def __init__(self, executor: Callable[[AIRequest], Awaitable[AIResult]]):
        self.executor = executor

    async def execute(self, request: AIRequest) -> AIResult:
        return await self.executor(request)


@dataclass(frozen=True)
class GraphRunContext:
    graph_run_id: UUID
    thread_id: UUID
    graph_key: str


RunContextResolver = Callable[[AIRequest], Awaitable[GraphRunContext]]


class LangGraphOrchestrationRuntime:
    """Durable runtime. Provider work remains delegated to AIOrchestrationService."""

    def __init__(self, *, checkpointer, handler: NodeHandler, run_context: RunContextResolver):
        self.checkpointer = checkpointer
        self.handler = handler
        self.run_context = run_context

    async def execute(self, request: AIRequest) -> AIResult:
        settings = get_langgraph_settings()
        settings.require_runtime()
        context = await self.run_context(request)
        definition = resolve_graph(context.graph_key)
        if context.graph_key == "regenerative-shadow-v1" and not settings.regenerative_shadow_enabled:
            raise RuntimeError("LANGGRAPH_REGENERATIVE_SHADOW_DISABLED")
        graph = build_graph(
            context.graph_key,
            handler=self.handler,
            checkpointer=self.checkpointer,
            timeout_seconds=settings.node_timeout_seconds,
        )
        initial: ScalpynGraphState = {
            "state_schema_version": definition.state_schema_version,
            "ai_request_id": str(request.ai_request_id),
            "tenant_id": str(request.tenant_id),
            "user_id": str(request.requested_by_user_id) if request.requested_by_user_id else None,
            "graph_run_id": str(context.graph_run_id),
            "graph_key": definition.graph_key,
            "graph_version": definition.semantic_version,
            "status": "RUNNING",
            "authority": request.authority.value,
            "completed_nodes": [],
            "event_keys": [],
            "tool_call_ids": [],
            "evidence_refs": [],
            "candidate_version_ids": [],
            "decision_memory_ids": [],
            "memory_hits": [],
            "recommendations": [],
            "warnings": [],
            "limitations": [],
        }
        assert_checkpoint_safe(initial)
        final_state = await graph.ainvoke(
            initial,
            config={"configurable": {
                "thread_id": str(context.thread_id),
                "checkpoint_ns": "scalpyn",
            }},
        )
        if final_state.get("__interrupt__"):
            raise RuntimeError("GRAPH_INTERRUPTED")
        result_json = final_state.get("result_json")
        if not result_json:
            raise RuntimeError("GRAPH_RESULT_MISSING")
        return AIResult.model_validate(result_json)
