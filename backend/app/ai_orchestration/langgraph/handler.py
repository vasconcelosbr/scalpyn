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
    AIRequestRecord, AIToolEvidenceRecord,
    AIResultRecord,
)
from ..hashing import canonical_hash
from ..errors import GraphNodeExecutionError
from ..module_registry import module_capability_registry
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
            run.last_completed_node = node_name

            event_payload: dict[str, Any] = {
                "state_schema_version": run.state_schema_version,
            }
            if node_name in {
                "retrieve_decision_memory", "retrieve_similar_decision_memory",
                "retrieve_contextual_memory", "load_audit_memory",
            }:
                memory_ids = updates.get("decision_memory_ids") or []
                event_payload.update({
                    "decision_memory_ids": memory_ids,
                    "memory_hit_count": len(memory_ids),
                })
            if node_name in {"execute_readonly_tools", "execute_tools", "assemble_evidence"}:
                tool_call_ids = updates.get("tool_call_ids") or []
                event_payload.update({
                    "tool_call_ids": tool_call_ids,
                    "tool_call_count": len(tool_call_ids),
                })
            if node_name == "invoke_provider":
                result_json = updates.get("result_json") or {}
                for key in (
                    "error_code", "terminal_reason", "provider_output_schema_valid",
                    "provider_stop_reason", "provider_response_ref", "schema_validator",
                    "provider_transport_attempted",
                ):
                    value = result_json.get(key)
                    if value is not None:
                        event_payload[key] = value[:160] if isinstance(value, str) else value
                schema_error_path = result_json.get("schema_error_path")
                if isinstance(schema_error_path, list):
                    event_payload["schema_error_path"] = [
                        str(item)[:96] for item in schema_error_path[:8]
                    ]
            if node_name in {
                "create_immutable_candidate_versions", "create_profile_candidate_version",
                "create_score_candidate_version",
            }:
                candidate_ids = updates.get("candidate_version_ids") or []
                event_payload.update({
                    "candidate_version_ids": candidate_ids,
                    "candidate_version_count": len(candidate_ids),
                })

            statement = insert(AIGraphEvent).values(
                tenant_id=run.tenant_id,
                graph_run_id=run.id,
                event_key=f"{run.id}:{node_name}:completed",
                event_type="NODE_COMPLETED",
                node_name=node_name,
                status="COMPLETED",
                payload=event_payload,
            ).on_conflict_do_nothing(
                index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]
            )
            await db.execute(statement)
            return updates

        with node_duration.labels(node_name=node_name).time():
            try:
                updates = await self._transaction(_handle)
            except GraphNodeExecutionError:
                raise
            except Exception as exc:
                raise GraphNodeExecutionError(node_name, exc) from exc
        checkpoint_writes.inc()
        if node_name in (state.get("completed_nodes") or []):
            node_retries.labels(node_name=node_name).inc()
        if node_name in {
            "retrieve_decision_memory", "retrieve_similar_decision_memory",
            "retrieve_contextual_memory", "load_audit_memory",
        }:
            decision_memory_hits.inc(len(updates.get("decision_memory_ids") or []))
        return updates

    async def _node_updates(
        self, db, run: AIGraphRun, request: AIRequestRecord,
        node_name: str, state: ScalpynGraphState,
    ) -> dict[str, Any]:
        if node_name in {"load_request", "detect_or_receive_degradation", "receive_degradation"}:
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
        if node_name == "resolve_origin_module":
            capability = module_capability_registry.get(request.origin_module)
            if capability is None or capability.status != "APPROVED":
                raise RuntimeError("GRAPH_ORIGIN_MODULE_INVALID")
            return {"evidence_refs": [{
                "kind": "module_capability",
                "reference": request.origin_module,
                "hash": capability.content_hash,
            }]}
        if node_name == "resolve_module_dependency_plan":
            capability = module_capability_registry[request.origin_module]
            modules = [request.origin_module, *(
                item for item in capability.dependencies if item in module_capability_registry
            )]
            return {"evidence_refs": [{
                "kind": "module_dependency_plan",
                "reference": value,
                "hash": module_capability_registry[value].content_hash,
            } for value in modules]}
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
        if node_name in {
            "freeze_canonical_dataset", "freeze_dataset", "freeze_comparable_dataset",
            "validate_dataset_and_bundle", "run_data_quality_gate",
        }:
            dataset = await db.get(AIDatasetSnapshotRecord, request.dataset_snapshot_id)
            if dataset is None or dataset.tenant_id != run.tenant_id:
                raise RuntimeError("GRAPH_DATASET_TENANT_SCOPE_MISMATCH")
            if dataset.quality_status not in {"PASS", "PASS_WITH_WARNINGS", "PASSED", "APPROVED", "VALID"}:
                raise RuntimeError("GRAPH_DATA_QUALITY_GATE_FAILED")
            return {"dataset_snapshot_id": str(dataset.id)}
        if node_name in {"resolve_configuration_bundle", "resolve_bundle"}:
            bundle = await db.get(AIConfigurationBundleRecord, request.configuration_bundle_id)
            if bundle is None or bundle.tenant_id != run.tenant_id:
                raise RuntimeError("GRAPH_CONFIGURATION_BUNDLE_INVALID")
            return {"configuration_bundle_id": str(bundle.id)}
        module_load_nodes = {
            "load_strategy_profiles": "strategy_profiles",
            "load_shadow": "shadow_portfolio",
            "load_score_engine": "score_engine",
            "load_global_risk": "global_risk",
            "load_strategies": "strategies",
            "load_ml_evidence": "ml_models",
            "load_social_score": "social_score",
            "load_market_regime": "market_regime",
        }
        if node_name in module_load_nodes:
            module_key = module_load_nodes[node_name]
            capability = module_capability_registry[module_key]
            return {"evidence_refs": [{
                "kind": "module_context_consulted",
                "reference": module_key,
                "hash": capability.content_hash,
            }]}
        if node_name in {
            "retrieve_decision_memory", "retrieve_similar_decision_memory",
            "retrieve_contextual_memory", "load_audit_memory",
        }:
            frozen_context = (request.request_json or {}).get("frozen_context") or {}
            context_fingerprint = frozen_context.get("context_fingerprint")
            mutation_fingerprint = frozen_context.get("mutation_fingerprint")
            if not context_fingerprint:
                return {"decision_memory_ids": [], "memory_hits": []}
            rows = (
                await db.execute(text("""
                    SELECT id, payload FROM decision_memory
                    WHERE tenant_id = :tenant_id
                      AND status IN ('APPROVED','COMPLETED','ACTIVE')
                      AND context_fingerprint = :context_fingerprint
                      AND (
                          CAST(:mutation_fingerprint AS text) IS NULL
                          OR mutation_fingerprint = CAST(:mutation_fingerprint AS text)
                      )
                    ORDER BY created_at DESC
                """), {
                    "tenant_id": run.tenant_id,
                    "context_fingerprint": context_fingerprint,
                    "mutation_fingerprint": mutation_fingerprint,
                })
            ).all()
            return {
                "decision_memory_ids": [str(row.id) for row in rows],
                "memory_hits": [{"id": str(row.id), "context_fingerprint": context_fingerprint} for row in rows],
            }
        if node_name == "classify_root_cause" and not state.get("root_cause_classification"):
            return {"root_cause_classification": "INSUFFICIENT_EVIDENCE"}
        if node_name == "create_hypothesis":
            hypothesis_id = await self._insert_regenerative_record(
                db, "decision_hypotheses", run, request,
                status="DRAFT", payload={"classification": state.get("root_cause_classification")},
            )
            return {"hypothesis_id": str(hypothesis_id)}
        if node_name in {"design_ablation_candidates", "design_ablation"}:
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
        if node_name in {"create_immutable_candidate_versions", "create_profile_candidate_version"}:
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
        if node_name == "create_score_candidate_version":
            if not state.get("candidate_version_ids"):
                raise RuntimeError("GRAPH_PROFILE_CANDIDATE_VERSION_REQUIRED")
            return {"candidate_version_ids": state.get("candidate_version_ids") or []}
        if node_name == "start_shadow_experiment":
            experiment_id = await self._insert_regenerative_record(
                db, "experiment_links", run, request, status="SHADOW_RUNNING",
                payload={"candidate_version_ids": state.get("candidate_version_ids") or []},
            )
            return {"experiment_id": str(experiment_id)}
        if node_name in {
            "apply_shadow_only_pointer_or_create_rollback_version",
            "keep_reject_or_create_rollback_version",
        }:
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
        if node_name in {"persist_experiment_outcome", "persist_outcome"}:
            await self._insert_regenerative_record(
                db, "regeneration_runs", run, request, status="COMPLETED_SHADOW_ONLY",
                payload={"experiment_id": state.get("experiment_id"), "live_write": False},
            )
            return {}
        if node_name in {"persist_decision_memory", "persist_contextual_memory"}:
            frozen_context = (request.request_json or {}).get("frozen_context") or {}
            memory_id = (
                await db.execute(text("""
                    INSERT INTO decision_memory (
                        tenant_id, ai_request_id, dataset_snapshot_id,
                        configuration_bundle_id, authority, status, payload,
                        mutation_fingerprint, context_fingerprint, context_json
                    ) VALUES (
                        :tenant_id, :request_id, :dataset_id, :bundle_id,
                        :authority, 'COMPLETED', CAST(:payload AS jsonb),
                        :mutation_fingerprint, :context_fingerprint, CAST(:context_json AS jsonb)
                    ) RETURNING id
                """), {
                    "tenant_id": run.tenant_id,
                    "request_id": request.id,
                    "dataset_id": request.dataset_snapshot_id,
                    "bundle_id": request.configuration_bundle_id,
                    "authority": run.authority,
                    "mutation_fingerprint": frozen_context.get("mutation_fingerprint"),
                    "context_fingerprint": frozen_context.get("context_fingerprint"),
                    "context_json": __import__("json").dumps(frozen_context.get("context") or {}, sort_keys=True),
                    "payload": __import__("json").dumps({
                        "experiment_id": state.get("experiment_id"),
                        "root_cause": state.get("root_cause_classification"),
                        "evidence_refs": state.get("evidence_refs") or [],
                    }, sort_keys=True),
                })
            ).scalar_one()
            return {"decision_memory_ids": [str(memory_id)]}
        if node_name in {"plan_typed_tools", "plan_tools"}:
            allowlist = (request.request_json or {}).get("tool_allowlist") or []
            declared = {
                name for capability in module_capability_registry.values()
                for name in capability.read_tools
            }
            if not isinstance(allowlist, list) or any(name not in declared for name in allowlist):
                raise RuntimeError("GRAPH_TOOL_ALLOWLIST_INVALID")
            return {"tool_plan": [{
                "name": name,
                "version": "1.0.0",
                "input": {"tenant_id": str(run.tenant_id), "filters": {}},
            } for name in allowlist]}
        if node_name in {"execute_readonly_tools", "execute_tools"}:
            from ..module_tool_runtime import ModuleToolRuntime

            dataset = await db.get(AIDatasetSnapshotRecord, request.dataset_snapshot_id)
            if dataset is None or dataset.tenant_id != run.tenant_id:
                raise RuntimeError("GRAPH_DATASET_TENANT_SCOPE_MISMATCH")
            runtime = ModuleToolRuntime()
            tool_call_ids: list[str] = []
            evidence_refs: list[dict[str, Any]] = []
            tools_called: list[str] = []
            for planned in state.get("tool_plan") or []:
                audit, output = await runtime.execute(
                    db,
                    tenant_id=run.tenant_id,
                    request=request,
                    dataset=dataset,
                    tool_name=planned["name"],
                    tool_input=planned["input"],
                )
                tool_call_ids.append(str(audit.id))
                tools_called.append(planned["name"])
                evidence_refs.append({
                    "kind": "typed_tool_output",
                    "reference": str(audit.id),
                    "hash": canonical_hash(output),
                })
            manifest = dict(dataset.context_manifest or {})
            manifest["tools_called"] = tools_called
            manifest["evidence_ids"] = list(dict.fromkeys([
                *(manifest.get("evidence_ids") or []),
                *tool_call_ids,
            ]))
            dataset.context_manifest = manifest
            return {"tool_call_ids": tool_call_ids, "evidence_refs": evidence_refs}
        if node_name in {"run_module_conflict_checks", "validate_risk_and_strategy"}:
            frozen = (request.request_json or {}).get("frozen_context") or {}
            proposed = frozen.get("proposed_changes") or []
            if node_name == "validate_risk_and_strategy" and not proposed:
                raise RuntimeError("GRAPH_REGENERATIVE_PROPOSED_CHANGES_REQUIRED")
            from ..recommendation_guard import GuardDecision, RecommendationGuard, RecommendationValidation

            risk_decision = GuardDecision.VETO if frozen.get("global_risk_veto") else GuardDecision.PASS
            strategy_decision = (
                GuardDecision.INVARIANT_CONFLICT
                if frozen.get("strategy_invariant_conflict") else GuardDecision.PASS
            )
            guard = RecommendationGuard.require_candidate_allowed(
                RecommendationValidation(module="global_risk", decision=risk_decision),
                RecommendationValidation(module="strategies", decision=strategy_decision),
            )
            if not guard.allowed:
                raise RuntimeError(guard.terminal_reason)
            return {"evidence_refs": [{
                "kind": "risk_strategy_guard",
                "reference": guard.terminal_reason,
                "hash": canonical_hash(guard.model_dump(mode="json")),
            }]}
        if node_name == "assemble_evidence":
            if state.get("tool_call_ids"):
                return {}
            plan_updates = await self._node_updates(db, run, request, "plan_tools", state)
            return await self._node_updates(
                db,
                run,
                request,
                "execute_tools",
                {**state, **plan_updates},
            )
        if node_name == "invoke_provider":
            from ...services.systemic_langgraph_bridge import SystemicLangGraphBridge
            return {"result_json": await SystemicLangGraphBridge.execute_prepared_request(db, request)}
        if node_name in {"validate_structured_output", "validate_output"}:
            if not isinstance(state.get("result_json"), dict):
                raise RuntimeError("GRAPH_RESULT_SCHEMA_INVALID")
            if state["result_json"].get("status") != "COMPLETED":
                reason_code = str(
                    state["result_json"].get("error_code") or "GRAPH_PROVIDER_RESULT_INVALID"
                )
                if state["result_json"].get("provider_transport_attempted") is True:
                    from ..errors import ProviderOutputError
                    raise ProviderOutputError(reason_code)
                raise RuntimeError(reason_code)
            recommendations = state["result_json"].get("recommendations") or []
            for recommendation in recommendations:
                side_effect = str(recommendation.get("side_effect_class") or "NONE")
                target_module = str(recommendation.get("target_module") or "")
                target_path = str(recommendation.get("target_path") or "")
                if side_effect == "LIVE_WRITE":
                    raise RuntimeError("GRAPH_LIVE_WRITE_RECOMMENDATION_DENIED")
                if target_module in {"global_risk", "strategies"} and side_effect != "NONE":
                    raise RuntimeError("GRAPH_GUARDRAIL_MODULE_MUTATION_DENIED")
                if target_module == "ml_models" and side_effect != "NONE":
                    raise RuntimeError("GRAPH_ML_MUTATION_DENIED")
                if any(value in target_path.lower() for value in ("train", "promote", "feature_contract", "label_contract")):
                    raise RuntimeError("GRAPH_ML_CONTRACT_MUTATION_DENIED")
            if request.authority in {"CANDIDATE_ONLY", "SHADOW_ONLY"} and recommendations:
                validator_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
                    AIToolEvidenceRecord.tenant_id == run.tenant_id,
                    AIToolEvidenceRecord.ai_request_id == request.id,
                    AIToolEvidenceRecord.tool_name.in_((
                        "global_risk.validate_recommendation",
                        "strategies.validate_recommendation",
                    )),
                ))).scalars().all())
                if {row.tool_name for row in validator_rows} != {
                    "global_risk.validate_recommendation",
                    "strategies.validate_recommendation",
                }:
                    raise RuntimeError("GRAPH_RISK_STRATEGY_VALIDATION_EVIDENCE_REQUIRED")
                if any(row.quality == "NO_DATA" for row in validator_rows):
                    raise RuntimeError("GRAPH_RISK_STRATEGY_VALIDATION_DATA_REQUIRED")
                from ..recommendation_guard import RecommendationGuard

                for recommendation in recommendations:
                    if recommendation.get("risk_conflicts"):
                        raise RuntimeError("GLOBAL_RISK_VETO")
                    if recommendation.get("strategy_conflicts"):
                        raise RuntimeError("STRATEGY_INVARIANT_CONFLICT")
                    spot_guard = RecommendationGuard.validate_spot_authority(
                        target_path=str(recommendation.get("target_path") or ""),
                        human_decision_id=(request.request_json or {}).get("human_decision_id"),
                    )
                    if not spot_guard.allowed:
                        raise RuntimeError(spot_guard.terminal_reason)
            return {}
        if node_name in {"persist_result_usage_audit", "persist_result"}:
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
