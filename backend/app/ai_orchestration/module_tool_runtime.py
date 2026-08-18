"""Bounded execution runtime for the immutable multi-module tool catalog."""

from __future__ import annotations

from typing import Any

from jsonschema import validate
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.systemic_ai import AIToolCallAudit, AIToolEvidenceRecord
from .contracts import Authority
from .domain_tools import default_tool_capabilities
from .hashing import canonical_hash
from .shadow_portfolio_handlers import SHADOW_PORTFOLIO_HANDLERS
from .tool_registry import ToolCapability, ToolRegistry


def _bounded_frozen_reader(
    capability: ToolCapability,
    *,
    rows: list[dict[str, Any]],
    dataset_hash: str,
    dataset_window_end: str,
) -> dict[str, Any]:
    """Return only canonical frozen rows; no tool can reach a live write path."""
    bounded = rows[: capability.max_rows or len(rows)]
    missingness = sorted({
        key
        for row in bounded
        for key, value in row.items()
        if value is None
    })
    quality = "NO_DATA" if not bounded else ("PASS_WITH_MISSINGNESS" if missingness else "PASS")
    return {
        "tool": capability.name,
        "contract_version": capability.version,
        "data": bounded,
        "evidence_ids": [str(row.get("id")) for row in bounded if row.get("id")],
        "freshness": {
            "dataset_window_end": dataset_window_end,
            "dataset_hash": dataset_hash,
            "sla_seconds": capability.freshness_sla_seconds,
        },
        "quality": quality,
        "missingness": missingness,
    }


class ModuleToolRuntime:
    """Authorize, validate, execute and audit one declared module tool."""

    def __init__(self) -> None:
        self.registry = ToolRegistry()
        for capability in default_tool_capabilities():
            handler = SHADOW_PORTFOLIO_HANDLERS.get(capability.name, _bounded_frozen_reader)
            self.registry.register(capability, handler)

    async def execute(
        self,
        db: AsyncSession,
        *,
        tenant_id,
        request,
        dataset,
        tool_name: str,
        tool_input: dict[str, Any],
        human_approval_id: str | None = None,
    ) -> tuple[AIToolCallAudit, dict[str, Any]]:
        capability = self.registry.authorize(
            tool_name,
            "1.0.0",
            authority=Authority(request.authority),
            permissions=frozenset({"ai:analyze"}),
        )
        if capability.requires_human_approval and not human_approval_id:
            raise RuntimeError("TOOL_HUMAN_APPROVAL_REQUIRED")
        if str(tool_input.get("tenant_id")) != str(tenant_id):
            raise RuntimeError("TOOL_TENANT_SCOPE_MISMATCH")
        validate(tool_input, capability.input_schema)

        from ..services.module_ai_analysis_service import ModuleAIAnalysisService
        from .langgraph.config import get_langgraph_settings

        request_json = request.request_json or {}
        dataset_request = request_json.get("dataset_request") or {}
        request_filters = dict(dataset_request.get("filters") or {})
        request_filters["max_rows"] = min(
            int(request_filters.get("max_rows") or get_langgraph_settings().tool_default_max_rows),
            int(capability.max_rows or 5_000),
        )
        entity_ids = tuple(request_filters.get("entity_ids") or ())
        if capability.domain != request.origin_module:
            entity_ids = ()
            if capability.domain == "ml_models":
                # As a supporting module (e.g. shadow_portfolio root-cause audit
                # consulting ml_models for context), only models that ever ran in
                # production are relevant -- "candidate"/"rejected" rows are
                # training runs that never shipped and have no bearing on why a
                # live trade behaved the way it did. When ml_models is the
                # request's own origin_module (someone directly analyzing model
                # training), the full registry stays unfiltered.
                request_filters = {**request_filters, "status_in": ("champion", "archived")}
        rows = await ModuleAIAnalysisService._rows(
            db,
            tenant_id=tenant_id,
            module_key=capability.domain,
            entity_ids=entity_ids,
            filters=request_filters,
        )
        handler = self.registry.handler(tool_name, "1.0.0")
        output = handler(
            capability,
            rows=rows,
            dataset_hash=dataset.dataset_hash,
            dataset_window_end=dataset.window_end.isoformat(),
        )
        validate(output, capability.output_schema)
        if isinstance(output.get("data"), list) and capability.max_rows is not None:
            if len(output["data"]) > capability.max_rows:
                raise RuntimeError("TOOL_MAX_ROWS_EXCEEDED")

        audit = AIToolCallAudit(
            tenant_id=tenant_id,
            ai_request_id=request.id,
            tool_name=capability.name,
            tool_version=capability.version,
            side_effect=capability.side_effect.value,
            status="COMPLETED",
            input_hash=canonical_hash(tool_input),
            output_hash=canonical_hash(output),
        )
        db.add(audit)
        await db.flush()
        db.add(AIToolEvidenceRecord(
            tenant_id=tenant_id,
            ai_request_id=request.id,
            tool_call_audit_id=audit.id,
            module_key=capability.domain,
            tool_name=capability.name,
            output_json=output,
            output_hash=canonical_hash(output),
            freshness_json=output.get("freshness"),
            quality=output["quality"],
        ))
        await db.flush()
        return audit, output
