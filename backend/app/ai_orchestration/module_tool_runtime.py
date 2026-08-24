"""Bounded execution runtime for the immutable multi-module tool catalog."""

from __future__ import annotations

from typing import Any

from jsonschema import validate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.config_profile import ConfigProfile
from ..models.systemic_ai import AIToolCallAudit, AIToolEvidenceRecord
from .contracts import Authority
from .domain_tools import default_tool_capabilities
from .hashing import canonical_hash
from .shadow_portfolio_handlers import SHADOW_PORTFOLIO_HANDLERS, canonical_analytics_rows
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
        request_filters = dict(
            dataset_request.get("filters")
            or (dataset.filters if isinstance(dataset.filters, dict) else {})
            or {}
        )
        evidence_origin_module = str(dataset.origin_module or request.origin_module)
        if capability.domain != "shadow_portfolio":
            request_filters["max_rows"] = min(
                int(request_filters.get("max_rows") or get_langgraph_settings().tool_default_max_rows),
                int(capability.max_rows or 5_000),
            )
        trade_entity_ids = tuple(request_filters.get("entity_ids") or ())
        entity_ids = trade_entity_ids
        if capability.domain != evidence_origin_module:
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
            elif capability.domain == "strategy_profiles" and evidence_origin_module == "shadow_portfolio" and trade_entity_ids:
                # entity_ids here are shadow_trade IDs, not profile IDs -- resolve
                # to the profiles actually referenced by this trade sample instead
                # of returning every tenant profile (most of which the sample
                # never touches).
                from uuid import UUID

                from ..models.shadow_trade import ShadowTrade

                trade_uuids = []
                for raw in trade_entity_ids:
                    try:
                        trade_uuids.append(UUID(raw))
                    except ValueError:
                        continue
                profile_ids = (await db.execute(
                    select(ShadowTrade.profile_id).distinct().where(
                        ShadowTrade.id.in_(trade_uuids),
                        ShadowTrade.profile_id.is_not(None),
                    )
                )).scalars().all()
                entity_ids = tuple(str(profile_id) for profile_id in profile_ids)
            elif capability.domain == "market_regime":
                # "get_current" and the other 4 market_regime tools all share this
                # generic handler and the same regime_history query -- none of them
                # take entity_ids, so as supporting context (not the analysis's own
                # subject) a handful of the most recent observations is the useful
                # size, not up to 200 rows sized for an unrelated trade sample.
                request_filters = {**request_filters, "max_rows": 5}
        governed_config_type = {
            "ml_models.get_governed_configuration": "ml",
            "social_score.get_governed_configuration": "social_score",
        }.get(tool_name)
        if governed_config_type is not None:
            records = list((await db.execute(select(ConfigProfile).where(
                ConfigProfile.user_id == tenant_id,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == governed_config_type,
                ConfigProfile.is_active.is_(True),
            ).order_by(ConfigProfile.updated_at.desc()).limit(1))).scalars().all())
            rows = [{
                "id": str(row.id),
                "event_identity": str(row.id),
                "outcome": "ACTIVE_CONFIGURATION",
                "lineage_status": "VERSIONED_BY_AUDIT",
                "config_type": row.config_type,
                "config": row.config_json,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            } for row in records]
        elif capability.domain == "shadow_portfolio":
            from ..services.shadow_full_canonical_service import (
                CONTRACT_VERSION as SHADOW_CANONICAL_CONTRACT_VERSION,
                load_canonical_items,
            )

            if dataset.contract_version != SHADOW_CANONICAL_CONTRACT_VERSION:
                raise RuntimeError("ANALYSIS_DATA_INCOMPLETE")
            canonical_rows = await load_canonical_items(
                db,
                tenant_id=tenant_id,
                dataset_snapshot_id=dataset.id,
            )
            if len(canonical_rows) != dataset.row_count:
                raise RuntimeError("DATASET_RECONCILIATION_FAILED")
            rows = (
                canonical_rows
                if tool_name == "shadow.freeze_analysis_dataset"
                else canonical_analytics_rows(canonical_rows)
            )
        else:
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
        if (
            capability.domain != "shadow_portfolio"
            and isinstance(output.get("data"), list)
            and capability.max_rows is not None
        ):
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
