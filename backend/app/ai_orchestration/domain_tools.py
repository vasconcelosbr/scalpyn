from __future__ import annotations

from .tool_registry import SideEffect, ToolCapability


def default_tool_capabilities() -> tuple[ToolCapability, ...]:
    names = (
        ("shadow.get_performance_summary", "shadow", 5_000),
        ("shadow.get_frozen_report", "shadow", 10_000),
        ("profiles.get_effective_configuration", "profiles", 1),
        ("profiles.get_version_history", "profiles", 500),
        ("scores.get_effective_configuration", "scores", 1),
        ("scores.get_version_history", "scores", 500),
        ("audit.get_change_lineage", "audit", 1_000),
        ("calibration.get_evidence", "calibration", 5_000),
        ("market_regime.get_history", "market_regime", 5_000),
        ("ml.get_authority_status", "ml", 100),
        ("bayesian.get_readonly_evidence", "bayesian", 5_000),
    )
    return tuple(ToolCapability(
        name=name, version="1.0.0", domain=domain,
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object"}, side_effect=SideEffect.NONE,
        required_permissions=("ai:analyze",), tenant_scoped=True,
        max_runtime_seconds=30, max_rows=max_rows, requires_human_approval=False,
    ) for name, domain, max_rows in names)
