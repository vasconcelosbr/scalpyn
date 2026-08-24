from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from .contracts import Authority, CanonicalDatasetRequest
from .errors import AIErrorCode, fail
from .hashing import canonical_hash
from .multimodule_contracts import AnalysisContextManifest, ModuleContextRefs


QUALITY_BLOCKS = {
    "BLOCK_DUPLICATE_IDENTITY",
    "BLOCK_CONFLICTING_OUTCOMES",
    "BLOCK_MISSING_LINEAGE",
    "BLOCK_STALE_DATA",
    "BLOCK_CONTRACT_MISMATCH",
}


class CanonicalAnalysisDataset(BaseModel):
    model_config = ConfigDict(frozen=True)
    dataset_snapshot_id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    contract_version: str = "systemic-multimodule-v1"
    origin_module: str
    module_context_refs: ModuleContextRefs = Field(default_factory=ModuleContextRefs)
    context_manifest: AnalysisContextManifest | None = None
    source_tables: tuple[str, ...]
    source_labels: tuple[str, ...]
    event_identity_contract: str
    outcome_contract: str
    time_anchor: str
    window_start: datetime
    window_end: datetime
    filters: dict[str, Any]
    exclusions: tuple[dict[str, Any], ...]
    row_count: int
    row_ids_hash: str
    query_hash: str
    dataset_hash: str
    configuration_bundle_id: UUID
    feature_contract_version: str | None = None
    label_contract_version: str | None = None
    quality_status: str
    quality_findings: tuple[dict[str, Any], ...]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CanonicalDatasetService:
    DOMAIN_TABLES = {
        "SHADOW_PORTFOLIO": ("shadow_trades", "shadow_trade_report_runs", "shadow_trade_report_items"),
        "PROFILE_INTELLIGENCE": ("shadow_trades", "profile_suggestions", "profile_indicator_stats"),
        "CALIBRATION": ("shadow_trades", "calibration_experiments", "ml_evidence_registry"),
        "ML_BAYESIAN": ("shadow_trades", "ml_evidence_registry", "bayesian_analysis_runs"),
        "MARKET_REGIME": ("regime_history", "indicator_snapshots"),
        "STRATEGY_PROFILES": ("profiles", "profile_versions"),
        "SCORE_ENGINE": ("config_profiles", "score_engine_versions", "rule_contribution"),
        "GLOBAL_RISK": ("config_profiles", "positions", "trades"),
        "STRATEGIES": ("config_profiles", "trades", "positions"),
        "INTELLIGENCE_RUNS": ("ai_graph_runs", "ai_graph_events", "ai_graph_interrupts"),
        "SOCIAL_SCORE": ("social_intelligence_runs", "social_asset_observations"),
        "AUDIT_VERSION_MEMORY": ("decision_memory", "decision_hypotheses", "ai_change_sets"),
    }

    def build(self, *, tenant_id: UUID, request: CanonicalDatasetRequest,
              configuration_bundle_id: UUID, rows: Iterable[dict[str, Any]], query_contract: dict[str, Any],
              feature_contract_version: str | None = None,
              label_contract_version: str | None = None,
              origin_module: str | None = None,
              module_context_refs: ModuleContextRefs | None = None,
              context_manifest: AnalysisContextManifest | None = None,
              contract_version: str = "systemic-multimodule-v1",
              dataset_content_hash: str | None = None,
              dataset_snapshot_id: UUID | None = None) -> CanonicalAnalysisDataset:
        frozen_rows = tuple(dict(row) for row in rows)
        if not frozen_rows:
            raise fail(AIErrorCode.DATASET_EMPTY, "Canonical dataset is empty")
        row_ids = [
            str(row.get("id") or (row.get("trade") or {}).get("id"))
            for row in frozen_rows
            if row.get("id") is not None or (row.get("trade") or {}).get("id") is not None
        ]
        findings: list[dict[str, Any]] = []
        identities: dict[str, set[str]] = {}
        missing_lineage = 0
        for row in frozen_rows:
            trade = row.get("trade") or {}
            identity = str(
                row.get("event_identity") or row.get("id")
                or trade.get("event_id") or trade.get("id") or ""
            )
            outcome = str(row.get("outcome") or trade.get("outcome") or trade.get("status") or "")
            if identity:
                identities.setdefault(identity, set()).add(outcome)
            if (row.get("lineage_status") or trade.get("lineage_status")) in (None, "", "UNRESOLVED"):
                missing_lineage += 1
        conflicts = sorted(identity for identity, outcomes in identities.items() if len(outcomes - {""}) > 1)
        duplicate_count = len(row_ids) - len(set(row_ids))
        if conflicts:
            findings.append({"code": "CONFLICTING_OUTCOMES", "count": len(conflicts), "excluded_identity_hash": canonical_hash(conflicts)})
            quality_status = "BLOCK_CONFLICTING_OUTCOMES"
        elif duplicate_count:
            findings.append({"code": "DUPLICATE_IDENTITY", "count": duplicate_count})
            quality_status = "BLOCK_DUPLICATE_IDENTITY"
        elif missing_lineage:
            findings.append({"code": "MISSING_LINEAGE", "count": missing_lineage})
            quality_status = "PASS_WITH_WARNINGS"
        else:
            quality_status = "PASS"
        dataset_payload = {
            "tenant_id": str(tenant_id), "contract_version": contract_version,
            "origin_module": origin_module or request.domain.lower(),
            "module_context_refs": (module_context_refs or ModuleContextRefs()).model_dump(mode="json"),
            "context_manifest": context_manifest.model_dump(mode="json") if context_manifest else None,
            "source_tables": self.DOMAIN_TABLES[request.domain], "source_labels": request.source_labels,
            "event_identity_contract": request.event_identity_contract, "outcome_contract": request.outcome_contract,
            "time_anchor": request.time_anchor, "window_start": request.window_start,
            "window_end": request.window_end, "filters": request.filters,
            "exclusions": request.exclusions, "row_ids_hash": canonical_hash(sorted(row_ids)),
            "query_hash": canonical_hash(query_contract), "configuration_bundle_id": str(configuration_bundle_id),
            "feature_contract_version": feature_contract_version, "label_contract_version": label_contract_version,
        }
        return CanonicalAnalysisDataset(
            **({"dataset_snapshot_id": dataset_snapshot_id} if dataset_snapshot_id else {}),
            tenant_id=tenant_id, contract_version=contract_version,
            origin_module=origin_module or request.domain.lower(),
            module_context_refs=module_context_refs or ModuleContextRefs(),
            context_manifest=context_manifest,
            source_tables=self.DOMAIN_TABLES[request.domain],
            source_labels=request.source_labels, event_identity_contract=request.event_identity_contract,
            outcome_contract=request.outcome_contract, time_anchor=request.time_anchor,
            window_start=request.window_start, window_end=request.window_end, filters=request.filters,
            exclusions=request.exclusions, row_count=len(frozen_rows), row_ids_hash=dataset_payload["row_ids_hash"],
            query_hash=dataset_payload["query_hash"],
            dataset_hash=dataset_content_hash or canonical_hash(dataset_payload),
            configuration_bundle_id=configuration_bundle_id,
            feature_contract_version=feature_contract_version, label_contract_version=label_contract_version,
            quality_status=quality_status, quality_findings=tuple(findings),
        )

    @staticmethod
    def require_comparable(left: CanonicalAnalysisDataset, right: CanonicalAnalysisDataset) -> None:
        fields = ("tenant_id", "source_tables", "source_labels", "event_identity_contract", "outcome_contract",
                  "time_anchor", "window_start", "window_end", "filters", "exclusions")
        if any(getattr(left, field) != getattr(right, field) for field in fields):
            raise fail(AIErrorCode.DATASET_CONTRACT_INVALID, "Dataset snapshots are not comparable")

    @staticmethod
    def enforce_quality(dataset: CanonicalAnalysisDataset, authority: Authority) -> None:
        if authority != Authority.ANALYSIS_ONLY and dataset.quality_status in QUALITY_BLOCKS:
            raise fail(AIErrorCode.DATASET_CONTRACT_INVALID, "Dataset quality blocks proposal or candidate authority")
