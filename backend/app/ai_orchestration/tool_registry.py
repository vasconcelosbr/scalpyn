from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from .contracts import Authority
from .errors import AIErrorCode, fail


class SideEffect(StrEnum):
    NONE = "NONE"
    AUDIT_WRITE = "AUDIT_WRITE"
    PROPOSAL_WRITE = "PROPOSAL_WRITE"
    CANDIDATE_WRITE = "CANDIDATE_WRITE"
    SHADOW_WRITE = "SHADOW_WRITE"
    LIVE_WRITE = "LIVE_WRITE"


class ToolCapability(BaseModel):
    model_config = ConfigDict(frozen=True)
    name: str
    version: str
    domain: str
    input_schema: dict
    output_schema: dict
    side_effect: SideEffect
    required_permissions: tuple[str, ...] = ()
    tenant_scoped: bool = True
    freshness_sla_seconds: int | None = None
    max_runtime_seconds: int
    max_rows: int | None = None
    requires_human_approval: bool = False


_ALLOWED_EFFECTS = {
    Authority.ANALYSIS_ONLY: {SideEffect.NONE, SideEffect.AUDIT_WRITE},
    Authority.PROPOSAL_ONLY: {SideEffect.NONE, SideEffect.AUDIT_WRITE, SideEffect.PROPOSAL_WRITE},
    Authority.CANDIDATE_ONLY: {SideEffect.NONE, SideEffect.AUDIT_WRITE, SideEffect.PROPOSAL_WRITE, SideEffect.CANDIDATE_WRITE},
    Authority.SHADOW_ONLY: {SideEffect.NONE, SideEffect.AUDIT_WRITE, SideEffect.PROPOSAL_WRITE, SideEffect.CANDIDATE_WRITE, SideEffect.SHADOW_WRITE},
}


class ToolRegistry:
    def __init__(self):
        self._capabilities: dict[str, ToolCapability] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, capability: ToolCapability, handler: Callable[..., Any]) -> None:
        key = f"{capability.name}@{capability.version}"
        if key in self._capabilities:
            raise ValueError(f"tool already registered: {key}")
        self._capabilities[key] = capability
        self._handlers[key] = handler

    def authorize(self, name: str, version: str, *, authority: Authority,
                  permissions: frozenset[str]) -> ToolCapability:
        capability = self._capabilities.get(f"{name}@{version}")
        if capability is None:
            raise fail(AIErrorCode.TOOL_NOT_ALLOWED, "Tool is not declared")
        if capability.side_effect == SideEffect.LIVE_WRITE or capability.side_effect not in _ALLOWED_EFFECTS[authority]:
            raise fail(AIErrorCode.TOOL_SIDE_EFFECT_DENIED, "Tool side effect exceeds request authority", http_status=403)
        if set(capability.required_permissions) - set(permissions):
            raise fail(AIErrorCode.TOOL_NOT_ALLOWED, "Tool permission is missing", http_status=403)
        return capability

    def handler(self, name: str, version: str) -> Callable[..., Any]:
        handler = self._handlers.get(f"{name}@{version}")
        if handler is None:
            raise fail(AIErrorCode.TOOL_NOT_ALLOWED, "Tool handler is not declared")
        return handler

    def export(self) -> list[dict]:
        return [item.model_dump(mode="json") for item in sorted(self._capabilities.values(), key=lambda x: x.name)]
