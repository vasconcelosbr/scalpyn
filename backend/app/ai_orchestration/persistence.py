from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.systemic_ai import (
    AIConfigurationBundleRecord, AIDatasetSnapshotRecord, AIJobRecord, AIModelResolutionRecord,
    AIRequestRecord, AIResultRecord, AIUsageRecord,
)
from .hashing import canonical_hash


class SQLAlchemyAIPersistence:
    """Stateful persistence hook for one orchestration request transaction."""

    def __init__(self, db: AsyncSession, tenant_id: UUID):
        self.db = db
        self.tenant_id = tenant_id
        self.resolution = None
        self.prompt = None
        self.request = None
        self.dataset_id = None
        self.bundle_id = None

    async def __call__(self, kind: str, value: Any) -> None:
        if kind == "model_resolution":
            self.resolution = value
            self.db.add(AIModelResolutionRecord(
                id=value.id, tenant_id=self.tenant_id,
                requested_provider=value.requested_provider, requested_model=value.requested_model,
                configured_provider=value.configured_provider, configured_model=value.configured_model,
                effective_provider=value.effective_provider, effective_model=value.effective_model,
                catalog_snapshot_hash=value.catalog_snapshot_hash, capabilities=list(value.capabilities),
                resolution_policy_version=value.resolution_policy_version,
                resolution_reason=value.resolution_reason, resolved_at=value.resolved_at,
            ))
        elif kind == "prompt":
            self.prompt = value
        elif kind == "configuration_bundle":
            self.bundle_id = value.configuration_bundle_id
            lineage = value.model_dump(mode="json", exclude={"bundle_json", "bundle_hash", "created_at", "configuration_bundle_id", "tenant_id", "profile_id", "profile_version_id", "score_engine_version_id"})
            self.db.add(AIConfigurationBundleRecord(
                id=value.configuration_bundle_id, tenant_id=value.tenant_id, profile_id=value.profile_id,
                profile_version_id=value.profile_version_id, score_engine_version_id=value.score_engine_version_id,
                lineage_refs=lineage, bundle_json=value.bundle_json, bundle_hash=value.bundle_hash,
                lineage_status=value.lineage_status, created_at=value.created_at,
            ))
        elif kind == "dataset":
            self.dataset_id = value.dataset_snapshot_id
            self.db.add(AIDatasetSnapshotRecord(
                id=value.dataset_snapshot_id, tenant_id=value.tenant_id, contract_version=value.contract_version,
                origin_module=value.origin_module,
                module_context_refs=value.module_context_refs.model_dump(mode="json"),
                context_manifest=value.context_manifest.model_dump(mode="json") if value.context_manifest else None,
                source_tables=list(value.source_tables), source_labels=list(value.source_labels),
                event_identity_contract=value.event_identity_contract, outcome_contract=value.outcome_contract,
                time_anchor=value.time_anchor, window_start=value.window_start, window_end=value.window_end,
                filters=value.filters, exclusions=list(value.exclusions), row_count=value.row_count,
                row_ids_hash=value.row_ids_hash, query_hash=value.query_hash, dataset_hash=value.dataset_hash,
                configuration_bundle_id=value.configuration_bundle_id, quality_status=value.quality_status,
                quality_findings=list(value.quality_findings), created_at=value.created_at,
            ))
        elif kind == "request":
            if self.resolution is None or self.prompt is None:
                raise RuntimeError("resolution and prompt must be persisted before request")
            self.request = value
            self.db.add(AIRequestRecord(
                id=value.ai_request_id, tenant_id=value.tenant_id, requested_by_user_id=value.requested_by_user_id,
                origin_module=value.origin_module, origin_view=value.origin_view,
                analysis_mode=value.analysis_mode.value, authority=value.authority.value,
                question_hash=canonical_hash(value.question), correlation_id=value.correlation_id,
                model_resolution_id=self.resolution.id, prompt_version_id=self.prompt.id,
                dataset_snapshot_id=self.dataset_id,
                configuration_bundle_id=self.bundle_id, request_json=value.model_dump(mode="json"),
                created_at=value.created_at,
            ))
        elif kind == "job":
            existing = await self.db.get(AIJobRecord, value.id)
            payload = dict(status=value.status.value, started_at=value.started_at, heartbeat_at=value.heartbeat_at,
                           lease_owner=value.lease_owner, lease_expires_at=value.lease_expires_at, attempt=value.attempt,
                           max_attempts=value.max_attempts, retry_after=value.retry_after, completed_at=value.completed_at,
                           terminal_reason=value.terminal_reason, last_error_code=str(value.last_error_code) if value.last_error_code else None,
                           last_error_safe_message=value.last_error_safe_message)
            if existing:
                for key, item in payload.items(): setattr(existing, key, item)
            else:
                self.db.add(AIJobRecord(id=value.id, tenant_id=value.tenant_id, ai_request_id=self.request.ai_request_id,
                                        purpose=value.purpose, dedupe_key=value.dedupe_key, queued_at=value.queued_at, **payload))
        elif kind == "result":
            self.db.add(AIResultRecord(tenant_id=value.tenant_id, ai_request_id=value.ai_request_id,
                                       status=value.status, result_json=value.model_dump(mode="json"),
                                       terminal_reason=value.terminal_reason, completed_at=value.completed_at))
            self.db.add(AIUsageRecord(
                tenant_id=value.tenant_id, ai_request_id=value.ai_request_id, provider=value.provider,
                model=value.effective_model, module=self.request.origin_module,
                tokens_input=value.usage.tokens_input, tokens_output=value.usage.tokens_output,
                estimated_cost=value.usage.estimated_cost, actual_cost=value.usage.actual_cost,
                currency=value.usage.currency, pricing_snapshot_version=value.usage.pricing_snapshot_version,
            ))
        await self.db.flush()
