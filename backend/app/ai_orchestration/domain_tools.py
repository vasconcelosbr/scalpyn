from __future__ import annotations

from .module_registry import module_capability_registry
from .tool_registry import SideEffect, ToolCapability


_CANDIDATE_TOOLS = {
    "strategy_profiles.create_candidate_version",
    "score_engine.create_candidate_version",
}
_SHADOW_TOOLS = {"shadow.create_experiment"}
_AUDIT_TOOLS = {
    "audit_memory.persist_hypothesis",
    "audit_memory.persist_decision_memory",
}


def _schemas(tool_name: str) -> tuple[dict, dict]:
    input_schema = {
        "type": "object",
        "properties": {
            "tenant_id": {"type": "string", "format": "uuid"},
            "entity_id": {"type": ["string", "null"]},
            "as_of": {"type": ["string", "null"], "format": "date-time"},
            "filters": {"type": "object"},
        },
        "required": ["tenant_id"],
        "additionalProperties": False,
    }
    output_schema = {
        "type": "object",
        "properties": {
            "tool": {"const": tool_name},
            "contract_version": {"type": "string"},
            "data": {"type": ["object", "array", "null"]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "freshness": {"type": ["object", "null"]},
            "quality": {"type": "string"},
            "missingness": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tool", "contract_version", "quality", "missingness"],
        "additionalProperties": False,
    }
    return input_schema, output_schema


def default_tool_capabilities() -> tuple[ToolCapability, ...]:
    capabilities: list[ToolCapability] = []
    for module in module_capability_registry.values():
        for name in (*module.read_tools, *module.write_tools):
            input_schema, output_schema = _schemas(name)
            if name in _CANDIDATE_TOOLS:
                side_effect = SideEffect.CANDIDATE_WRITE
            elif name in _SHADOW_TOOLS:
                side_effect = SideEffect.SHADOW_WRITE
            elif name in _AUDIT_TOOLS:
                side_effect = SideEffect.AUDIT_WRITE
            else:
                side_effect = SideEffect.NONE
            capabilities.append(ToolCapability(
                name=name,
                version="1.0.0",
                domain=module.module_key,
                input_schema=input_schema,
                output_schema=output_schema,
                side_effect=side_effect,
                required_permissions=("ai:analyze",),
                tenant_scoped=True,
                freshness_sla_seconds=module.freshness_sla_seconds,
                max_runtime_seconds=30,
                max_rows=5_000 if side_effect is SideEffect.NONE else 1,
                requires_human_approval=side_effect in {SideEffect.CANDIDATE_WRITE, SideEffect.SHADOW_WRITE},
            ))
    return tuple(sorted(capabilities, key=lambda item: item.name))
