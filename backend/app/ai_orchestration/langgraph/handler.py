"""Canonical database-backed node handler for durable graph execution.

The handler deliberately stores only identifiers, hashes, and bounded JSON in
checkpoint state. Provider credentials never cross this boundary. A provider
result must already exist in the canonical ``ai_results`` table; invoking a
paid provider remains the responsibility of the audited central orchestrator
and its explicit production/cost gate.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from ...database import run_db_task
from ...models.ai_graph import AIGraphEvent, AIGraphRun
from ...models.systemic_ai import (
    AIConfigurationBundleRecord,
    AIDatasetSnapshotRecord,
    AIModelResolutionRecord,
    AIPromptVersion,
    AIRequestRecord,
    AIResultRecord,
)
from .config import get_langgraph_settings
from .metrics import checkpoint_writes, decision_memory_hits, node_duration, node_retries
from .state import ScalpynGraphState


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CanonicalGraphNodeHandler:
    """Execute graph nodes against tenant-scoped canonical records."""

    def __init__(self, graph_run_id: UUID, *, celery: bool = True):
        self.graph_run_id = graph_run_id
        self.celery = celery

    async def _transaction(self, fn):
        return await run_db_task(fn, celery=self.celery)

    async def handle(self, node_name: str, state: ScalpynGraphState) -> dict[str, Any]:
        async def _handle(db):
            run = (
                await db.execute(
                    select(AIGraphRun).where(AIGraphRun.id == self.graph_run_id).with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise RuntimeError("GRAPH_RUN_NOT_FOUND")
            if run.status == "CANCELLED":
                raise RuntimeError("GRAPH_RUN_CANCELLED")
            if str(run.tenant_id) != state.get("tenant_id"):
                raise RuntimeError("GRAPH_TENANT_SCOPE_MISMATCH")

            request = await db.get(AIRequestRecord, run.ai_request_id)
            if request is None or request.tenant_id != run.tenant_id:
                raise RuntimeError("GRAPH_REQUEST_TENANT_SCOPE_MISMATCH")

            now = _now()
            settings = get_langgraph_settings()
            run.status = "RUNNING"
            run.current_node = node_name
            run.heartbeat_at = now
            run.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
            run.updated_at = now
            updates = await self._node_updates(db, run, request, node_name, state)

            statement = insert(AIGraphEvent).values(
                tenant_id=run.tenant_id,
                graph_run_id=run.id,
                event_key=f"{run.id}:{node_name}:completed",
                event_type="NODE_COMPLETED",
                node_name=node_name,
                status="RUNNING",
                payload={"state_schema_version": run.state_schema_version},
            ).on_conflict_do_nothing(
                index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]
            )
            await db.execute(statement)
            return updates

        with node_duration.labels(node_name=node_name).time():
            updates = await self._transaction(_handle)
        checkpoint_writes.inc()
        if node_name in (state.get("completed_nodes") or []):
            node_retries.labels(node_name=node_name).inc()
        if node_name in {"retrieve_decision_memory", "retrieve_similar_decision_memory"}:
            decision_memory_hits.inc(len(updates.get("decision_memory_ids") or []))
        return updates

    async def _node_updates(
        self, db, run: AIGraphRun, request: AIRequestRecord,
        node_name: str, state: ScalpynGraphState,
    ) -> dict[str, Any]:
        if node_name in {"load_request", "detect_or_receive_degradation"}:
            return {
                "ai_request_id": str(request.id),
                "dataset_snapshot_id": str(request.dataset_snapshot_id),
                "configuration_bundle_id": str(request.configuration_bundle_id),
            }
        if node_name == "authorize_tenant":
            if request.authority not in {
                "ANALYSIS_ONLY", "PROPOSAL_ONLY", "CANDIDATE_ONLY", "SHADOW_ONLY",
            }:
                raise RuntimeError("GRAPH_AUTHORITY_DENIED")
            return {"authority": request.authority}
        if node_name == "resolve_provider_model":
            resolution = await db.get(AIModelResolutionRecord, request.model_resolution_id)
            if resolution is None or resolution.tenant_id != run.tenant_id:
                raise RuntimeError("GRAPH_MODEL_RESOLUTION_INVALID")
            return {
                "model_resolution_id": str(resolution.id),
                "configured_model": resolution.configured_model,
                "effective_model": resolution.effective_model,
            }
        if node_name == "resolve_prompt":
            prompt = await db.get(AIPromptVersion, request.prompt_version_id)
            if prompt is None or prompt.status != "APPROVED":
                raise RuntimeError("GRAPH_PROMPT_NOT_APPROVED")
            return {"prompt_version_id": str(prompt.id), "prompt_hash": prompt.content_hash}
        if node_name in {"freeze_canonical_dataset", "validate_dataset_and_bundle", "run_data_quality_gate"}:
            dataset = await db.get(AIDatasetSnapshotRecord, request.dataset_snapshot_id)
            if dataset is None or dataset.tenant_id != run.tenant_id:
                raise RuntimeError("GRAPH_DATASET_TENANT_SCOPE_MISMATCH")
            if dataset.quality_status not in {"PASS", "PASSED", "APPROVED", "VALID"}:
                raise RuntimeError("GRAPH_DATA_QUALITY_GATE_FAILED")
            return {"dataset_snapshot_id": str(dataset.id)}
        if node_name == "resolve_configuration_bundle":
            bundle = await db.get(AIConfigurationBundleRecord, request.configuration_bundle_id)
            if bundle is None or bundle.tenant_id != run.tenant_id:
                raise RuntimeError("GRAPH_CONFIGURATION_BUNDLE_INVALID")
            return {"configuration_bundle_id": str(bundle.id)}
        if node_name in {"retrieve_decision_memory", "retrieve_similar_decision_memory"}:
            rows = (
                await db.execute(text("""
                    SELECT id FROM decision_memory
                    WHERE tenant_id = :tenant_id
                      AND status IN ('APPROVED','COMPLETED','ACTIVE')
                    ORDER BY created_at DESC LIMIT 20
                """), {"tenant_id": run.tenant_id})
            ).scalars().all()
            return {"decision_memory_ids": [str(value) for value in rows]}
        if node_name == "classify_root_cause" and not state.get("root_cause_classification"):
            return {"root_cause_classification": "INSUFFICIENT_EVIDENCE"}
        if node_name == "create_hypothesis":
            hypothesis_id = await self._insert_regenerative_record(
                db, "decision_hypotheses", run, request,
                status="DRAFT", payload={"classification": state.get("root_cause_classification")},
            )
            return {"hypothesis_id": str(hypothesis_id)}
        if node_name == "design_ablation_candidates":
            candidate_config = (request.request_json or {}).get("candidate_config")
            score_config = (request.request_json or {}).get("score_config")
            if not isinstance(candidate_config, dict) or not isinstance(score_config, dict):
                raise RuntimeError("GRAPH_ABLATION_CANDIDATE_INPUT_REQUIRED")
            change_set_id = await self._insert_regenerative_record(
                db, "ai_change_sets", run, request, status="PENDING_HUMAN_APPROVAL",
                payload={
                    "candidate_config": candidate_config,
                    "score_config": score_config,
                    "mutation_reason": (request.request_json or {}).get("mutation_reason")
                    or "systemic_regenerative_shadow_ablation",
                    "live_write": False,
                },
            )
            return {"change_set_id": str(change_set_id)}
        if node_name == "create_immutable_candidate_versions":
            decision = state.get("interrupt_decision") or {}
            candidate_ids = ((decision.get("edits") or {}).get("candidate_version_ids") or [])
            if candidate_ids:
                rows = (
                    await db.execute(text("""
                        SELECT pv.id FROM profile_versions pv
                        JOIN profiles p ON p.id = pv.profile_id
                        WHERE pv.id = ANY(CAST(:ids AS uuid[]))
                          AND pv.status IN ('CANDIDATE','SHADOW')
                          AND p.user_id = :tenant_id
                    """), {"ids": [str(value) for value in candidate_ids], "tenant_id": run.tenant_id})
                ).scalars().all()
                if {str(value) for value in rows} != {str(value) for value in candidate_ids}:
                    raise RuntimeError("GRAPH_CANDIDATE_VERSION_VALIDATION_FAILED")
                return {"candidate_version_ids": [str(value) for value in rows]}
            bundle = await db.get(AIConfigurationBundleRecord, request.configuration_bundle_id)
            if bundle is None or bundle.profile_id is None or bundle.tenant_id != run.tenant_id:
                raise RuntimeError("GRAPH_CANDIDATE_PROFILE_REQUIRED")
            change_set_id = state.get("change_set_id")
            if not change_set_id:
                raise RuntimeError("GRAPH_CHANGE_SET_REQUIRED")
            candidate_config = (request.request_json or {}).get("candidate_config")
            score_config = (request.request_json or {}).get("score_config")
            if not isinstance(candidate_config, dict) or not isinstance(score_config, dict):
                raise RuntimeError("GRAPH_ABLATION_CANDIDATE_INPUT_REQUIRED")
            from ...services.profile_versioning_v2 import create_candidate_profile_version
            version_id, _score_id, _created = await create_candidate_profile_version(
                db,
                profile_id=bundle.profile_id,
                config=candidate_config,
                score_config=score_config,
                change_set_id=UUID(str(change_set_id)),
                mutation_reason=(request.request_json or {}).get("mutation_reason")
                or "systemic_regenerative_shadow_ablation",
            )
            return {"candidate_version_ids": [str(version_id)]}
        if node_name == "start_shadow_experiment":
            experiment_id = await self._insert_regenerative_record(
                db, "experiment_links", run, request, status="SHADOW_RUNNING",
                payload={"candidate_version_ids": state.get("candidate_version_ids") or []},
            )
            return {"experiment_id": str(experiment_id)}
        if node_name == "apply_shadow_only_pointer_or_create_rollback_version":
            if run.authority != "SHADOW_ONLY":
                raise RuntimeError("GRAPH_SHADOW_ONLY_AUTHORITY_REQUIRED")
            change_set_id = await self._insert_regenerative_record(
                db, "ai_change_sets", run, request, status="PROPOSED_SHADOW_ONLY",
                payload={
                    "candidate_version_ids": state.get("candidate_version_ids") or [],
                    "live_write": False,
                    "decision": state.get("interrupt_decision") or {},
                },
            )
            return {"change_set_id": str(change_set_id)}
        if node_name == "persist_experiment_outcome":
            await self._insert_regenerative_record(
                db, "regeneration_runs", run, request, status="COMPLETED_SHADOW_ONLY",
                payload={"experiment_id": state.get("experiment_id"), "live_write": False},
            )
            return {}
        if node_name == "persist_decision_memory":
            memory_id = await self._insert_regenerative_record(
                db, "decision_memory", run, request, status="DRAFT",
                payload={
                    "experiment_id": state.get("experiment_id"),
                    "root_cause": state.get("root_cause_classification"),
                    "evidence_refs": state.get("evidence_refs") or [],
                },
            )
            return {"decision_memory_ids": [str(memory_id)]}
        if node_name == "invoke_provider":
            existing = (
                await db.execute(select(AIResultRecord).where(
                    AIResultRecord.tenant_id == run.tenant_id,
                    AIResultRecord.ai_request_id == request.id,
                ))
            ).scalar_one_or_none()
            if existing is None:
                raise RuntimeError("GRAPH_PROVIDER_RESULT_NOT_AVAILABLE")
            return {"result_json": existing.result_json}
        if node_name == "validate_structured_output":
            if not isinstance(state.get("result_json"), dict):
                raise RuntimeError("GRAPH_RESULT_SCHEMA_INVALID")
            return {}
        if node_name == "persist_result_usage_audit":
            existing = (
                await db.execute(select(AIResultRecord.id).where(
                    AIResultRecord.tenant_id == run.tenant_id,
                    AIResultRecord.ai_request_id == request.id,
                ))
            ).scalar_one_or_none()
            if existing is None and run.authority != "SHADOW_ONLY":
                raise RuntimeError("GRAPH_CANONICAL_RESULT_MISSING")
            return {}
        if node_name == "complete":
            return {"status": "COMPLETED", "terminal_reason": "GRAPH_COMPLETED"}

        # Read-only analysis nodes contribute audit events but have no implicit
        # mutation authority. Domain tools are added only through the approved
        # central tool registry.
        return {}

    @staticmethod
    async def _insert_regenerative_record(
        db, table_name: str, run: AIGraphRun, request: AIRequestRecord,
        *, status: str, payload: dict[str, Any],
    ):
        allowed = {
            "decision_hypotheses", "ai_change_sets", "regeneration_runs",
            "experiment_links", "decision_memory",
        }
        if table_name not in allowed:
            raise RuntimeError("GRAPH_REGENERATIVE_TABLE_DENIED")
        return (
            await db.execute(text(f"""
                INSERT INTO {table_name} (
                    tenant_id, ai_request_id, dataset_snapshot_id,
                    configuration_bundle_id, authority, status, payload
                ) VALUES (
                    :tenant_id, :request_id, :dataset_id, :bundle_id,
                    :authority, :status, CAST(:payload AS jsonb)
                ) RETURNING id
            """), {
                "tenant_id": run.tenant_id,
                "request_id": request.id,
                "dataset_id": request.dataset_snapshot_id,
                "bundle_id": request.configuration_bundle_id,
                "authority": run.authority,
                "status": status,
                "payload": __import__("json").dumps(payload, sort_keys=True),
            })
        ).scalar_one()
