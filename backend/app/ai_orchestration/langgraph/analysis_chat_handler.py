"""Durable node handler for the derived Analysis Chat graph."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from typing import Any
import uuid
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ...database import run_db_task
from ...ai_orchestration.budget_reservation_audit import BudgetReservationAudit
from ...ai_orchestration.provider_adapters import anthropic_output_config
from ...ai_orchestration.sanitizer import TrustLabel, structured_block
from ...models.ai_graph import AIGraphEvent, AIGraphRun
from ...models.analysis_chat import (
    AIAnalysisConversation,
    AIAnalysisMessage,
    AIAnalysisMessageEvidence,
)
from ...models.config_profile import ConfigProfile
from ...models.ai_provider_key import AIProviderKey
from ...models.systemic_ai import (
    AIBudgetPolicyRecord,
    AIBudgetReservationRecord,
    AIDatasetSnapshotRecord,
    AIModelApprovalRecord,
    AIModelResolutionRecord,
    AIPromptVersion,
    AIRequestRecord,
    AIResultRecord,
    AIToolEvidenceRecord,
    AIUsageRecord,
)
from ...schemas.analysis_chat import AnalysisChatOutput, AnalysisChatRuntimeConfig
from ...services.ai_keys_service import decrypt_value
from ...services.governed_change_service import (
    approve_and_execute as approve_and_execute_governed_change,
    create_dry_run as create_governed_change_dry_run,
)
from ...services.systemic_langgraph_bridge import SystemicLangGraphBridge
from ..errors import (
    GraphNodeExecutionError,
    ProviderBlockedError,
    ProviderOutputError,
    ProviderTransportError,
)
from .state import ScalpynGraphState


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_provider_parent(
    provider_answer: AnalysisChatOutput,
    canonical_parent_id: UUID,
) -> AnalysisChatOutput:
    if provider_answer.parent_analysis_run_id == canonical_parent_id:
        return provider_answer
    return provider_answer.model_copy(update={
        "parent_analysis_run_id": canonical_parent_id,
        "warnings": [
            *provider_answer.warnings,
            "PROVIDER_PARENT_ANALYSIS_RUN_ID_NORMALIZED",
        ],
    })


class AnalysisChatGraphNodeHandler:
    def __init__(self, graph_run_id: UUID, *, celery: bool = True):
        self.graph_run_id = graph_run_id
        self.celery = celery

    async def _transaction(self, fn):
        return await run_db_task(fn, celery=self.celery)

    async def handle(self, node_name: str, state: ScalpynGraphState) -> dict[str, Any]:
        async def _handle(db):
            run = (
                await db.execute(select(AIGraphRun).where(
                    AIGraphRun.id == self.graph_run_id
                ).with_for_update())
            ).scalar_one_or_none()
            if run is None:
                raise RuntimeError("ANALYSIS_CHAT_GRAPH_RUN_NOT_FOUND")
            request = await db.get(AIRequestRecord, run.ai_request_id)
            if request is None or request.tenant_id != run.tenant_id:
                raise RuntimeError("ANALYSIS_CHAT_REQUEST_SCOPE_INVALID")
            message = (
                await db.execute(select(AIAnalysisMessage).where(
                    AIAnalysisMessage.ai_request_id == request.id,
                    AIAnalysisMessage.role == "ASSISTANT",
                    AIAnalysisMessage.tenant_id == run.tenant_id,
                ).with_for_update())
            ).scalar_one_or_none()
            if message is None:
                raise RuntimeError("ANALYSIS_CHAT_MESSAGE_NOT_FOUND")
            if message.status == "CANCELLED":
                raise RuntimeError("ANALYSIS_CHAT_MESSAGE_CANCELLED")
            # ``persist_message_result_usage`` terminalizes the canonical
            # message before the summary and completion nodes run.  Those
            # bookkeeping nodes must never regress it back to STREAMING.
            if message.status not in {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}:
                message.status = "STREAMING"
                message.lock_version = int(message.lock_version or 0) + 1
            updates = await self._node_updates(db, run, request, message, node_name, state)
            await db.execute(insert(AIGraphEvent).values(
                tenant_id=run.tenant_id,
                graph_run_id=run.id,
                event_key=f"{run.id}:{node_name}:completed",
                event_type="NODE_COMPLETED",
                node_name=node_name,
                status="COMPLETED",
                payload={
                    "conversation_id": str(request.conversation_id),
                    "message_id": str(message.id),
                    "data_mode": request.request_json.get("data_mode"),
                    "evidence_count": len(
                        updates.get("selected_evidence_refs")
                        or updates.get("evidence_refs") or []
                    ),
                },
            ).on_conflict_do_nothing(
                index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]
            ))
            return updates

        try:
            return await self._transaction(_handle)
        except GraphNodeExecutionError:
            raise
        except Exception as exc:
            raise GraphNodeExecutionError(node_name, exc) from exc

    async def _node_updates(
        self,
        db,
        run: AIGraphRun,
        request: AIRequestRecord,
        message: AIAnalysisMessage,
        node_name: str,
        state: ScalpynGraphState,
    ) -> dict[str, Any]:
        request_json = dict(request.request_json or {})
        conversation = await db.get(AIAnalysisConversation, request.conversation_id)
        if conversation is None or conversation.tenant_id != run.tenant_id:
            raise RuntimeError("ANALYSIS_CHAT_CONVERSATION_SCOPE_INVALID")

        if node_name == "load_conversation":
            return {
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "parent_analysis_run_id": str(conversation.parent_analysis_run_id),
                "parent_result_id": str(conversation.parent_result_id),
                "request_intent": request_json.get("request_intent"),
                "request_kind": request.request_kind,
                "data_mode": request_json.get("data_mode") or "FROZEN_ANALYSIS_ONLY",
                "question": str(request_json.get("question") or ""),
            }
        if node_name == "authorize_tenant":
            expected_authority = (
                "PROPOSAL_ONLY"
                if request_json.get("data_mode") == "DRAFT_PROPOSAL"
                else "ANALYSIS_ONLY"
            )
            if request.authority != expected_authority or run.authority != expected_authority:
                raise RuntimeError("ANALYSIS_CHAT_AUTHORITY_DENIED")
            if request.parent_analysis_run_id != conversation.parent_analysis_run_id:
                raise RuntimeError("ANALYSIS_CHAT_PARENT_LINK_MISMATCH")
            return {"authority": expected_authority}
        if node_name == "load_parent_analysis":
            parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
            result = await db.get(AIResultRecord, conversation.parent_result_id)
            if (
                parent_run is None or result is None
                or parent_run.tenant_id != run.tenant_id or result.tenant_id != run.tenant_id
            ):
                raise RuntimeError("ANALYSIS_CHAT_PARENT_SCOPE_INVALID")
            document = result.result_json if isinstance(result.result_json, dict) else {}
            analysis = document.get("analysis") if isinstance(document.get("analysis"), dict) else {}
            summary = (
                analysis.get("executive_summary")
                or analysis.get("summary")
                or document.get("terminal_reason")
                or "Análise original concluída; consulte as evidências vinculadas."
            )
            return {"parent_result_summary": str(summary)[:3000]}
        if node_name == "validate_parent_contracts":
            parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
            result = await db.get(AIResultRecord, conversation.parent_result_id)
            if parent_run is None or result is None or parent_run.status != "COMPLETED" or result.status != "COMPLETED":
                raise RuntimeError("ANALYSIS_CHAT_PARENT_NOT_COMPLETED")
            if result.ai_request_id != parent_run.ai_request_id:
                raise RuntimeError("ANALYSIS_CHAT_PARENT_RESULT_MISMATCH")
            return {}
        if node_name == "load_conversation_memory":
            config = await self._runtime_config(db, run.tenant_id)
            rows = list((await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.tenant_id == run.tenant_id,
                AIAnalysisMessage.conversation_id == conversation.id,
                AIAnalysisMessage.sequence_number < message.sequence_number,
            ).order_by(AIAnalysisMessage.sequence_number.desc()).limit(
                config.recent_message_limit
            ))).scalars().all())
            return {
                "conversation_summary": conversation.running_summary,
                "recent_messages": [{
                    "sequence": row.sequence_number,
                    "role": row.role,
                    "content": (row.content or "")[:2000],
                    "content_hash": row.content_hash,
                } for row in reversed(rows)],
            }
        if node_name == "classify_followup":
            mode = request_json.get("data_mode") or "FROZEN_ANALYSIS_ONLY"
            reason = {
                "FROZEN_ANALYSIS_ONLY": "FROZEN_CONTEXT_SUFFICIENT",
                "ALLOW_READONLY_REFRESH": "READONLY_REFRESH_REQUIRED",
                "CREATE_CHILD_ANALYSIS": "CHILD_ANALYSIS_REQUIRED",
                "DRAFT_PROPOSAL": "PROPOSAL_CONFIRMATION_REQUIRED",
            }.get(mode, "AMBIGUOUS_REQUEST_DEFAULTED_SAFE")
            return {"data_mode": mode if mode in {
                "FROZEN_ANALYSIS_ONLY", "ALLOW_READONLY_REFRESH",
                "CREATE_CHILD_ANALYSIS", "DRAFT_PROPOSAL",
            } else "FROZEN_ANALYSIS_ONLY", "reason_code": reason}
        if node_name == "select_data_mode":
            return {"data_mode": state.get("data_mode") or "FROZEN_ANALYSIS_ONLY"}
        if node_name == "plan_readonly_tools":
            allowlist = request_json.get("tool_allowlist") or []
            if allowlist != ["market_regime.get_current"]:
                raise RuntimeError("ANALYSIS_CHAT_READONLY_TOOL_ALLOWLIST_INVALID")
            return {"tool_plan": [{
                "name": "market_regime.get_current",
                "input": {"tenant_id": str(run.tenant_id), "filters": {}},
            }]}
        if node_name == "execute_readonly_tools":
            from ..module_tool_runtime import ModuleToolRuntime

            dataset = await db.get(AIDatasetSnapshotRecord, request.dataset_snapshot_id)
            if dataset is None or dataset.tenant_id != run.tenant_id:
                raise RuntimeError("ANALYSIS_CHAT_DATASET_SCOPE_INVALID")
            runtime = ModuleToolRuntime()
            call_ids: list[str] = []
            refs: list[dict[str, Any]] = []
            for planned in state.get("tool_plan") or []:
                audit, _output = await runtime.execute(
                    db,
                    tenant_id=run.tenant_id,
                    request=request,
                    dataset=dataset,
                    tool_name=planned["name"],
                    tool_input=planned["input"],
                )
                call_ids.append(str(audit.id))
                refs.append({
                    "kind": "REFRESHED_READONLY_DATA",
                    "tool_call_id": str(audit.id),
                    "module": "market_regime",
                    "tool": planned["name"],
                    "queried_at": _now().isoformat(),
                })
            return {
                "readonly_tool_call_ids": call_ids,
                "new_data_snapshot_refs": refs,
                "tool_call_ids": call_ids,
            }
        if node_name == "retrieve_relevant_evidence":
            parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
            rows = list((await db.execute(select(AIToolEvidenceRecord).where(
                AIToolEvidenceRecord.tenant_id == run.tenant_id,
                AIToolEvidenceRecord.ai_request_id == parent_run.ai_request_id,
            ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id).limit(12))).scalars().all())
            refreshed = list((await db.execute(select(AIToolEvidenceRecord).where(
                AIToolEvidenceRecord.tenant_id == run.tenant_id,
                AIToolEvidenceRecord.ai_request_id == request.id,
            ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id).limit(4))).scalars().all())
            refs = [{
                "evidence_id": str(row.id),
                "module": row.module_key,
                "label": row.tool_name,
                "source_timestamp": row.created_at.isoformat() if row.created_at else None,
                "source": "FROZEN_ANALYSIS",
            } for row in rows]
            refs.extend({
                "evidence_id": str(row.id),
                "module": row.module_key,
                "label": row.tool_name,
                "source_timestamp": row.created_at.isoformat() if row.created_at else None,
                "source": "REFRESHED_READONLY_DATA",
            } for row in refreshed)
            return {"selected_evidence_refs": refs, "evidence_refs": refs}
        if node_name == "decide_if_new_data_required":
            return {"new_data_queried": state.get("data_mode") == "ALLOW_READONLY_REFRESH"}
        if node_name == "create_child_analysis_if_confirmed":
            answer = AnalysisChatOutput(
                answer=(
                    "A confirmação foi registrada, mas a criação da análise filha permaneceu "
                    "bloqueada porque este runtime não pode reutilizar o dataset ou o Configuration "
                    "Bundle do run pai. Nenhuma análise filha foi criada."
                ),
                answer_type="LIMITATION",
                based_on="CHILD_ANALYSIS",
                parent_analysis_run_id=conversation.parent_analysis_run_id,
                limitations=["CHILD_ANALYSIS_REQUIRES_FRESH_DATASET_AND_BUNDLE"],
                suggested_questions=["Quais contratos seriam congelados em uma análise filha?"],
            ).model_dump(mode="json")
            return {"answer": answer, "limitations": answer["limitations"]}
        if node_name == "draft_proposal_if_confirmed":
            answer = dict(state.get("answer") or {})
            raw_proposal = answer.get("proposal")
            if not isinstance(raw_proposal, dict):
                answer["answer_type"] = "LIMITATION"
                answer["limitations"] = [
                    *(answer.get("limitations") or []),
                    "GOVERNED_CHANGE_REQUIRES_AN_UNAMBIGUOUS_TYPED_TARGET",
                ]
                return {"answer": answer, "proposal_id": None}
            changes: list[dict[str, Any]] = []
            for raw_change in raw_proposal.get("changes") or []:
                change = dict(raw_change)
                if change.get("op") != "remove":
                    try:
                        change["value"] = json.loads(str(change.pop("value_json")))
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError(
                            f"Invalid value_json for governed change path {change.get('path')}"
                        ) from exc
                else:
                    change.pop("value_json", None)
                changes.append(change)
            typed_proposal = {
                "operation_type": raw_proposal.get("operation_type"),
                "target": raw_proposal.get("target") or {},
                "objective": raw_proposal.get("objective"),
                "risk": raw_proposal.get("risk"),
                "changes": changes,
            }
            evidence_ids = {
                str(ref.get("evidence_id"))
                for ref in state.get("selected_evidence_refs") or []
                if ref.get("evidence_id")
            }
            plan = await create_governed_change_dry_run(
                db,
                run.tenant_id,
                proposal=typed_proposal,
                conversation_id=conversation.id,
                message_id=message.id,
                evidence_ids=evidence_ids,
            )
            proposal_id = UUID(plan["proposal_id"])
            message.proposal_id = proposal_id
            answer["answer"] = (
                "A prévia foi validada. Revise o diff abaixo; a configuração só será "
                "alterada depois da confirmação final."
            )
            answer["answer_type"] = "PROPOSAL"
            answer["based_on"] = "PROPOSAL_DRAFT"
            answer["proposal"] = plan
            answer["warnings"] = [
                *(answer.get("warnings") or []),
                "GLOBAL_RISK_AND_STRATEGIES_VALIDATION_PENDING",
            ]
            return {"proposal_id": str(proposal_id), "answer": answer}
        if node_name == "validate_risk_and_strategy":
            from ..module_tool_runtime import ModuleToolRuntime

            expected = [
                "global_risk.validate_recommendation",
                "strategies.validate_recommendation",
            ]
            if request_json.get("tool_allowlist") != expected:
                raise RuntimeError("ANALYSIS_CHAT_PROPOSAL_VALIDATOR_POLICY_INVALID")
            dataset = await db.get(AIDatasetSnapshotRecord, request.dataset_snapshot_id)
            if dataset is None or dataset.tenant_id != run.tenant_id:
                raise RuntimeError("ANALYSIS_CHAT_DATASET_SCOPE_INVALID")
            runtime = ModuleToolRuntime()
            validation_refs: list[dict[str, Any]] = []
            validation_quality: dict[str, str] = {}
            tool_call_ids: list[str] = []
            for tool_name in expected:
                audit, output = await runtime.execute(
                    db,
                    tenant_id=run.tenant_id,
                    request=request,
                    dataset=dataset,
                    tool_name=tool_name,
                    tool_input={"tenant_id": str(run.tenant_id), "filters": {}},
                )
                tool_call_ids.append(str(audit.id))
                evidence = (await db.execute(select(AIToolEvidenceRecord).where(
                    AIToolEvidenceRecord.tool_call_audit_id == audit.id,
                    AIToolEvidenceRecord.tenant_id == run.tenant_id,
                ))).scalar_one()
                validation_quality[tool_name] = str(output.get("quality") or "NO_DATA")
                validation_refs.append({
                    "evidence_id": str(evidence.id),
                    "module": evidence.module_key,
                    "label": tool_name,
                    "source_timestamp": evidence.created_at.isoformat() if evidence.created_at else None,
                    "source": "REFRESHED_READONLY_DATA",
                })
            answer = dict(state.get("answer") or {})
            proposal = dict(answer.get("proposal") or {})
            vetoed = any(value == "NO_DATA" for value in validation_quality.values())
            proposal.update({
                "risk_validation": (
                    "VETO_NO_DATA" if validation_quality[expected[0]] == "NO_DATA"
                    else "PASS_READONLY_EVIDENCE"
                ),
                "strategy_validation": (
                    "VETO_NO_DATA" if validation_quality[expected[1]] == "NO_DATA"
                    else "PASS_READONLY_EVIDENCE"
                ),
                "candidate_created": False,
                "shadow_started": False,
            })
            answer["proposal"] = proposal
            answer["evidence_refs"] = [*(answer.get("evidence_refs") or []), *validation_refs]
            answer["modules_consulted"] = ["global_risk", "strategies"]
            if vetoed:
                answer["warnings"] = [*(answer.get("warnings") or []), "PROPOSAL_VALIDATION_VETO_NO_DATA"]
            return {
                "answer": answer,
                "evidence_refs": validation_refs,
                "selected_evidence_refs": validation_refs,
                "tool_call_ids": tool_call_ids,
                "readonly_tool_call_ids": tool_call_ids,
            }
        if node_name == "execute_governed_proposal_if_confirmed":
            proposal_id = state.get("proposal_id")
            decision = dict(state.get("interrupt_decision") or {})
            # Editing never implies approval. A changed request must produce a
            # fresh preview before the user can approve its exact diff.
            if not proposal_id or decision.get("decision") != "approve":
                raise RuntimeError("ANALYSIS_CHAT_GOVERNED_CHANGE_APPROVAL_MISSING")
            actor_user_id = decision.get("actor_user_id")
            if not actor_user_id or str(actor_user_id) != str(request.requested_by_user_id):
                raise RuntimeError("ANALYSIS_CHAT_GOVERNED_CHANGE_ACTOR_MISMATCH")
            executed = await approve_and_execute_governed_change(
                db,
                run.tenant_id,
                UUID(str(proposal_id)),
                decision_id=decision.get("decision_id"),
            )
            answer = dict(state.get("answer") or {})
            answer["answer"] = (
                "Alteração confirmada e aplicada com registro de auditoria e rollback disponível."
            )
            answer["proposal"] = executed
            answer["limitations"] = [
                item for item in answer.get("limitations") or []
                if item != "DRAFT_NOT_APPLIED"
            ]
            return {"answer": answer, "proposal_execution_result": executed}
        if node_name == "assemble_chat_context":
            return {
                "limitations": list(state.get("limitations") or []),
                "warnings": list(state.get("warnings") or []),
            }
        if node_name == "reserve_budget":
            await self._ensure_reservation(db, run, request)
            return {}
        if node_name == "invoke_provider":
            intent = str(request_json.get("request_intent") or "")
            if intent == "FAKE_PROVIDER_CANARY":
                environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower()
                fake_enabled = os.getenv("LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "false").lower() == "true"
                if "staging" not in environment or not fake_enabled:
                    raise ProviderBlockedError(
                        "FAKE_PROVIDER_CANARY_DISABLED",
                        "Fake provider transport is allowed only in the governed staging environment",
                    )
                refs = list(state.get("selected_evidence_refs") or [])
                modules = list(dict.fromkeys(str(item.get("module")) for item in refs if item.get("module")))
                based_on = (
                    "REFRESHED_READONLY_DATA"
                    if state.get("data_mode") == "ALLOW_READONLY_REFRESH"
                    else "FROZEN_ANALYSIS"
                )
                answer_type = "READONLY_REFRESH" if based_on == "REFRESHED_READONLY_DATA" else "EXPLANATION"
                is_proposal = state.get("data_mode") == "DRAFT_PROPOSAL"
                summary = str(state.get("parent_result_summary") or "A análise original foi concluída.")
                if based_on == "REFRESHED_READONLY_DATA":
                    answer_text = (
                        f"Resultado original congelado: {summary[:700]} "
                        "Consulta nova separada: foi lido somente o snapshot atual de market_regime. "
                        "A janela histórica de sete dias não foi materializada por esta tool; nenhuma "
                        "configuração foi alterada."
                    )
                else:
                    answer_text = (
                        f"Com base no resultado persistido da análise original: {summary[:900]} "
                        "A resposta está limitada às evidências vinculadas abaixo e não altera nenhuma configuração."
                    )
                answer = AnalysisChatOutput(
                    answer=answer_text[:5000],
                    answer_type="LIMITATION" if is_proposal else answer_type,
                    based_on="PROPOSAL_DRAFT" if is_proposal else based_on,
                    parent_analysis_run_id=conversation.parent_analysis_run_id,
                    modules_consulted=modules,
                    evidence_refs=refs,
                    new_data_queried=based_on == "REFRESHED_READONLY_DATA",
                    new_data_window=(
                        {
                            "requested_window": "7d",
                            "effective_coverage": "CURRENT_SNAPSHOT_ONLY",
                            "queried_at": _now().isoformat(),
                        }
                        if based_on == "REFRESHED_READONLY_DATA" else None
                    ),
                    limitations=[
                        "STAGING_FAKE_PROVIDER_RESPONSE",
                        *(["FAKE_PROVIDER_DOES_NOT_CREATE_EXECUTABLE_CHANGES"] if is_proposal else []),
                        *(["READONLY_REFRESH_CURRENT_SNAPSHOT_ONLY"]
                          if based_on == "REFRESHED_READONLY_DATA" else []),
                    ],
                    suggested_questions=[
                        "Quais evidências sustentam a principal conclusão?",
                        "Quais limitações materiais permanecem?",
                    ],
                ).model_dump(mode="json")
                await self._emit_tokens(db, run, request, message, answer["answer"])
                return {"answer": answer, "provider_transport_attempted": False}

            runtime_config = (
                await db.execute(select(ConfigProfile).where(
                    ConfigProfile.user_id == run.tenant_id,
                    ConfigProfile.pool_id.is_(None),
                    ConfigProfile.config_type == "ai_provider_runtime",
                    ConfigProfile.is_active.is_(True),
                ).order_by(ConfigProfile.updated_at.desc()).limit(1))
            ).scalar_one_or_none()
            enabled = bool(
                runtime_config
                and (runtime_config.config_json or {}).get("normal_analysis_provider_enabled") is True
            )
            if not enabled:
                raise ProviderBlockedError(
                    "NORMAL_ANALYSIS_PROVIDER_DISABLED",
                    "The tenant-governed normal analysis provider gate is disabled",
                )
            answer = await self._invoke_normal_provider(
                db, run, request, message, conversation, state
            )
            return {
                "answer": answer.model_dump(mode="json"),
                "provider_transport_attempted": True,
            }
        if node_name == "validate_chat_output":
            AnalysisChatOutput.model_validate(state.get("answer"))
            return {}
        if node_name == "persist_message_result_usage":
            answer = AnalysisChatOutput.model_validate(state.get("answer"))
            message.tool_call_ids_json = list(state.get("tool_call_ids") or [])
            await self._persist_answer(db, run, request, message, conversation, answer)
            return {"result_json": answer.model_dump(mode="json")}
        if node_name == "update_conversation_summary_if_needed":
            config = await self._runtime_config(db, run.tenant_id)
            if not config.summary_enabled or int(conversation.message_count or 0) < config.summary_message_threshold:
                return {}
            rows = list((await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.tenant_id == run.tenant_id,
                AIAnalysisMessage.conversation_id == conversation.id,
                AIAnalysisMessage.sequence_number > int(conversation.summarized_through_sequence or 0),
                AIAnalysisMessage.status == "COMPLETED",
            ).order_by(AIAnalysisMessage.sequence_number).limit(20))).scalars().all())
            if not rows:
                return {}
            summary = "\n".join(
                f"{row.sequence_number}:{row.role}:{(row.content or '')[:500]}" for row in rows
            )[:6000]
            conversation.running_summary = summary
            conversation.summary_version = "analysis-chat-summary@1.0.0"
            conversation.summary_hash = _sha(summary)
            conversation.summarized_through_sequence = rows[-1].sequence_number
            return {"conversation_summary": summary}
        if node_name == "complete_message":
            return {"status": "COMPLETED", "terminal_reason": "ANALYSIS_CHAT_MESSAGE_COMPLETED"}
        return {}

    async def _invoke_normal_provider(
        self,
        db,
        run: AIGraphRun,
        request: AIRequestRecord,
        message: AIAnalysisMessage,
        conversation: AIAnalysisConversation,
        state: ScalpynGraphState,
    ) -> AnalysisChatOutput:
        request_json = dict(request.request_json or {})
        resolution = await db.get(AIModelResolutionRecord, request.model_resolution_id)
        prompt = await db.get(AIPromptVersion, request.prompt_version_id)
        parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
        parent_result = await db.get(AIResultRecord, conversation.parent_result_id)
        if not all((resolution, prompt, parent_run, parent_result)):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_CANONICAL_LINEAGE_INVALID",
                "The chat provider was blocked because canonical lineage is incomplete",
            )
        if (
            resolution.tenant_id != run.tenant_id
            or parent_run.tenant_id != run.tenant_id
            or parent_result.tenant_id != run.tenant_id
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_TENANT_SCOPE_INVALID",
                "The chat provider was blocked by tenant scope validation",
            )

        try:
            approval_id = UUID(str(request_json["model_approval_id"]))
        except (KeyError, ValueError) as exc:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_MODEL_APPROVAL_REQUIRED",
                "The chat provider requires an auditable per-turn approval",
            ) from exc
        approval = await db.get(AIModelApprovalRecord, approval_id)
        if (
            approval is None
            or approval.tenant_id != run.tenant_id
            or approval.provider != resolution.effective_provider
            or approval.model != resolution.effective_model
            or approval.scope != "ANALYSIS_CHAT_TURN"
            or approval.status != "APPROVED"
            or approval.expires_at <= _now()
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_MODEL_APPROVAL_INVALID",
                "The chat provider was blocked because the per-turn approval is invalid or expired",
            )
        budget = (
            await db.execute(select(AIBudgetPolicyRecord).where(
                AIBudgetPolicyRecord.tenant_id == run.tenant_id,
                AIBudgetPolicyRecord.provider == resolution.effective_provider,
                AIBudgetPolicyRecord.model == resolution.effective_model,
                AIBudgetPolicyRecord.module == "analysis_chat",
                AIBudgetPolicyRecord.is_active.is_(True),
            ))
        ).scalar_one_or_none()
        if (
            budget is None
            or budget.null_limit_policy != "DENY"
            or budget.daily_token_limit is None
            or budget.monthly_token_limit is None
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_BOUNDED_BUDGET_REQUIRED",
                "The chat provider requires a bounded request, daily and monthly budget",
            )
        key = (
            await db.execute(select(AIProviderKey).where(
                AIProviderKey.user_id == run.tenant_id,
                AIProviderKey.provider == resolution.effective_provider,
                AIProviderKey.is_active.is_(True),
                AIProviderKey.is_validated.is_(True),
            ))
        ).scalar_one_or_none()
        if key is None or key.monthly_token_limit is None:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_VALIDATED_PROVIDER_KEY_REQUIRED",
                "The chat provider requires an active validated key with a monthly limit",
            )

        evidence_ids: list[UUID] = []
        for ref in state.get("selected_evidence_refs") or []:
            try:
                evidence_ids.append(UUID(str(ref.get("evidence_id"))))
            except (TypeError, ValueError):
                continue
        evidence_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
            AIToolEvidenceRecord.tenant_id == run.tenant_id,
            AIToolEvidenceRecord.id.in_(evidence_ids),
        ))).scalars().all()) if evidence_ids else []
        by_id = {row.id: row for row in evidence_rows}
        ordered_evidence = [by_id[item] for item in evidence_ids if item in by_id]
        if not ordered_evidence:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_TYPED_EVIDENCE_REQUIRED",
                "The chat provider requires tenant-scoped typed evidence",
            )
        evidence_payload = [{
            "evidence_id": str(row.id),
            "module": row.module_key,
            "tool": row.tool_name,
            "output": row.output_json,
        } for row in ordered_evidence]
        values = {
            "parent_analysis": json.dumps(
                parent_result.result_json,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ),
            "evidence": json.dumps(
                evidence_payload,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ),
            "conversation": json.dumps({
                "summary": state.get("conversation_summary"),
                "recent_messages": state.get("recent_messages") or [],
            }, ensure_ascii=False, default=str, separators=(",", ":")),
            "question": structured_block(
                TrustLabel.USER_INPUT, str(request_json.get("question") or "")
            ),
        }
        try:
            system_prompt = prompt.system_template.format_map(values)
            user_prompt = prompt.user_template.format_map(values)
        except KeyError as exc:
            raise ProviderBlockedError(
                f"ANALYSIS_CHAT_PROMPT_INPUT_MISSING_{exc.args[0]}",
                "The chat provider prompt contract is incomplete",
            ) from exc
        structured_output_reservation = 0
        if resolution.effective_provider == "anthropic":
            structured_output_reservation = len(json.dumps(
                {"output_config": anthropic_output_config(prompt.output_schema_json)},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")) + 512
        estimated_input_tokens = max(
            1,
            len((system_prompt + user_prompt).encode("utf-8"))
            + structured_output_reservation,
        )
        max_output_tokens = int(approval.max_output_tokens)
        max_input_tokens = int(budget.request_token_limit) - max_output_tokens
        if max_input_tokens <= 0 or estimated_input_tokens > max_input_tokens:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_INPUT_RESERVATION_EXCEEDED",
                "The chat provider was blocked because the input reservation exceeds the request budget",
            )
        reserved_tokens = estimated_input_tokens + max_output_tokens
        now = _now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        used_today = int((await db.scalar(select(func.coalesce(
            func.sum(AIUsageRecord.tokens_input + AIUsageRecord.tokens_output), 0,
        )).where(
            AIUsageRecord.tenant_id == run.tenant_id,
            AIUsageRecord.provider == resolution.effective_provider,
            AIUsageRecord.model == resolution.effective_model,
            AIUsageRecord.module == "analysis_chat",
            AIUsageRecord.created_at >= day_start,
        ))) or 0)
        used_month = int((await db.scalar(select(func.coalesce(
            func.sum(AIUsageRecord.tokens_input + AIUsageRecord.tokens_output), 0,
        )).where(
            AIUsageRecord.tenant_id == run.tenant_id,
            AIUsageRecord.provider == resolution.effective_provider,
            AIUsageRecord.model == resolution.effective_model,
            AIUsageRecord.module == "analysis_chat",
            AIUsageRecord.created_at >= month_start,
        ))) or 0)
        if used_today + reserved_tokens > int(budget.daily_token_limit):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_DAILY_TOKEN_BUDGET_EXCEEDED",
                "The chat provider was blocked by the daily token budget",
            )
        if used_month + reserved_tokens > int(budget.monthly_token_limit):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_MONTHLY_TOKEN_BUDGET_EXCEEDED",
                "The chat provider was blocked by the monthly token budget",
            )
        if int(key.tokens_used_month or 0) + reserved_tokens > int(key.monthly_token_limit):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_PROVIDER_KEY_BUDGET_EXCEEDED",
                "The chat provider was blocked by the provider-key monthly budget",
            )
        million = Decimal("1000000")
        input_rate = Decimal(approval.input_cost_per_million)
        output_rate = Decimal(approval.output_cost_per_million)
        reserved_cost = (
            Decimal(estimated_input_tokens) * input_rate
            + Decimal(max_output_tokens) * output_rate
        ) / million
        if reserved_cost > Decimal(approval.max_cost_usd):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_COST_APPROVAL_EXCEEDED",
                "The chat provider was blocked by the per-turn cost ceiling",
            )

        activated = await run_db_task(
            lambda reservation_db: BudgetReservationAudit.activate_placeholder(
                reservation_db,
                tenant_id=run.tenant_id,
                ai_request_id=request.id,
                budget_policy_id=budget.id,
                model_approval_id=approval.id,
                provider=resolution.effective_provider,
                model=resolution.effective_model,
                module="analysis_chat",
                estimated_input_tokens=estimated_input_tokens,
                max_output_tokens=max_output_tokens,
                request_token_limit=int(budget.request_token_limit),
                daily_token_limit=int(budget.daily_token_limit),
                monthly_token_limit=int(budget.monthly_token_limit),
                reserved_tokens=reserved_tokens,
                reserved_cost_usd=reserved_cost,
            ),
            celery=True,
        )
        if not activated["activated"]:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_BUDGET_RESERVATION_ALREADY_ACTIVATED_NO_RETRY",
                "The chat provider was not retried because this turn already has an activated reservation",
            )
        try:
            await run_db_task(
                lambda reservation_db: BudgetReservationAudit.mark_transport_started(
                    reservation_db,
                    tenant_id=run.tenant_id,
                    ai_request_id=request.id,
                ),
                celery=True,
            )
        except Exception:
            await run_db_task(
                lambda reservation_db: BudgetReservationAudit.release_before_transport(
                    reservation_db,
                    tenant_id=run.tenant_id,
                    ai_request_id=request.id,
                    reason_code="ANALYSIS_CHAT_TRANSPORT_START_AUDIT_FAILED",
                ),
                celery=True,
            )
            raise
        try:
            response = await SystemicLangGraphBridge.execute_json_provider(
                provider=resolution.effective_provider,
                model=resolution.effective_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                api_key=decrypt_value(bytes(key.api_key_encrypted)),
                request_id=str(request.id),
                max_output_tokens=max_output_tokens,
                output_schema=prompt.output_schema_json,
            )
        except Exception as exc:
            await run_db_task(
                lambda reservation_db: BudgetReservationAudit.mark_transport_error(
                    reservation_db,
                    tenant_id=run.tenant_id,
                    ai_request_id=request.id,
                    reason_code="ANALYSIS_CHAT_PROVIDER_TRANSPORT_FAILED",
                ),
                celery=True,
            )
            raise ProviderTransportError("ANALYSIS_CHAT_PROVIDER_TRANSPORT_FAILED") from exc

        actual_tokens = int(response.tokens_input) + int(response.tokens_output)
        actual_cost = (
            Decimal(response.tokens_input) * input_rate
            + Decimal(response.tokens_output) * output_rate
        ) / million
        await run_db_task(
            lambda reservation_db: BudgetReservationAudit.reconcile(
                reservation_db,
                tenant_id=run.tenant_id,
                ai_request_id=request.id,
                reserved_tokens=reserved_tokens,
                actual_tokens=actual_tokens,
                actual_cost_usd=actual_cost,
                terminal_reason=str(response.terminal_error_code or "PROVIDER_RESPONSE_RECEIVED"),
            ),
            celery=True,
        )
        usage_audit = await run_db_task(
            lambda usage_db: self._record_provider_usage(
                usage_db,
                tenant_id=run.tenant_id,
                ai_request_id=request.id,
                provider=resolution.effective_provider,
                model=resolution.effective_model,
                tokens_input=int(response.tokens_input),
                tokens_output=int(response.tokens_output),
                estimated_cost=reserved_cost,
                actual_cost=actual_cost,
                pricing_snapshot_version=approval.pricing_snapshot_hash,
            ),
            celery=True,
        )
        reconciliation_error = next((code for exceeded, code in (
            (actual_tokens > int(budget.request_token_limit), "ANALYSIS_CHAT_REQUEST_RECONCILIATION_EXCEEDED"),
            (used_today + actual_tokens > int(budget.daily_token_limit), "ANALYSIS_CHAT_DAILY_RECONCILIATION_EXCEEDED"),
            (used_month + actual_tokens > int(budget.monthly_token_limit), "ANALYSIS_CHAT_MONTHLY_RECONCILIATION_EXCEEDED"),
            (int(usage_audit["provider_tokens_used_month"]) > int(key.monthly_token_limit), "ANALYSIS_CHAT_PROVIDER_KEY_RECONCILIATION_EXCEEDED"),
            (actual_cost > Decimal(approval.max_cost_usd), "ANALYSIS_CHAT_COST_RECONCILIATION_EXCEEDED"),
        ) if exceeded), None)
        if response.terminal_error_code is not None:
            raise ProviderOutputError(str(response.terminal_error_code))
        if reconciliation_error is not None:
            raise ProviderTransportError(reconciliation_error)
        try:
            provider_answer = AnalysisChatOutput.model_validate(response.output)
        except Exception as exc:
            raise ProviderOutputError("ANALYSIS_CHAT_OUTPUT_SCHEMA_INVALID") from exc
        provider_answer = _normalize_provider_parent(
            provider_answer,
            conversation.parent_analysis_run_id,
        )

        refs = list(state.get("selected_evidence_refs") or [])
        modules = list(dict.fromkeys(
            str(item.get("module")) for item in refs if item.get("module")
        ))
        refreshed = state.get("data_mode") == "ALLOW_READONLY_REFRESH"
        answer = AnalysisChatOutput(
            answer=provider_answer.answer,
            answer_type="READONLY_REFRESH" if refreshed else "EXPLANATION",
            based_on="REFRESHED_READONLY_DATA" if refreshed else "FROZEN_ANALYSIS",
            parent_analysis_run_id=conversation.parent_analysis_run_id,
            modules_consulted=modules,
            evidence_refs=refs,
            new_data_queried=refreshed,
            new_data_window=provider_answer.new_data_window,
            warnings=provider_answer.warnings,
            limitations=provider_answer.limitations,
            suggested_questions=provider_answer.suggested_questions,
        )
        await self._emit_tokens(db, run, request, message, answer.answer)
        return answer

    async def _record_provider_usage(
        self,
        db,
        *,
        tenant_id: UUID,
        ai_request_id: UUID,
        provider: str,
        model: str,
        tokens_input: int,
        tokens_output: int,
        estimated_cost: Decimal,
        actual_cost: Decimal,
        pricing_snapshot_version: str,
    ) -> dict[str, int]:
        existing = (
            await db.execute(select(AIUsageRecord).where(
                AIUsageRecord.tenant_id == tenant_id,
                AIUsageRecord.ai_request_id == ai_request_id,
            ).with_for_update())
        ).scalar_one_or_none()
        key = (
            await db.execute(select(AIProviderKey).where(
                AIProviderKey.user_id == tenant_id,
                AIProviderKey.provider == provider,
                AIProviderKey.is_active.is_(True),
                AIProviderKey.is_validated.is_(True),
            ).with_for_update())
        ).scalar_one_or_none()
        if key is None:
            raise RuntimeError("ANALYSIS_CHAT_PROVIDER_KEY_DISAPPEARED_AFTER_TRANSPORT")
        if existing is None:
            db.add(AIUsageRecord(
                tenant_id=tenant_id,
                ai_request_id=ai_request_id,
                provider=provider,
                model=model,
                module="analysis_chat",
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                estimated_cost=estimated_cost,
                actual_cost=actual_cost,
                currency="USD",
                pricing_snapshot_version=pricing_snapshot_version,
            ))
            key.tokens_used_month = (
                int(key.tokens_used_month or 0) + tokens_input + tokens_output
            )
            key.last_used_at = _now()
        elif (
            int(existing.tokens_input) != tokens_input
            or int(existing.tokens_output) != tokens_output
            or Decimal(existing.actual_cost) != actual_cost
        ):
            raise RuntimeError("ANALYSIS_CHAT_PROVIDER_USAGE_CONFLICT")
        await db.flush()
        return {"provider_tokens_used_month": int(key.tokens_used_month or 0)}

    async def _runtime_config(self, db, tenant_id: UUID) -> AnalysisChatRuntimeConfig:
        record = (
            await db.execute(select(ConfigProfile).where(
                ConfigProfile.user_id == tenant_id,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == "ai_analysis_chat_runtime",
                ConfigProfile.is_active.is_(True),
            ).order_by(ConfigProfile.updated_at.desc()).limit(1))
        ).scalar_one_or_none()
        return AnalysisChatRuntimeConfig.model_validate(record.config_json if record else {})

    async def _ensure_reservation(self, db, run, request) -> AIBudgetReservationRecord:
        existing = (
            await db.execute(select(AIBudgetReservationRecord).where(
                AIBudgetReservationRecord.ai_request_id == request.id
            ).with_for_update())
        ).scalar_one_or_none()
        if existing:
            return existing
        intent = str((request.request_json or {}).get("request_intent") or "NORMAL_ANALYSIS")
        provider = "fake" if intent == "FAKE_PROVIDER_CANARY" else "blocked"
        model = "fake-analysis-v1" if provider == "fake" else "pending-checkpoint"
        reservation = AIBudgetReservationRecord(
            id=uuid.uuid4(),
            tenant_id=run.tenant_id,
            ai_request_id=request.id,
            request_intent=intent,
            provider=provider,
            model=model,
            module="analysis_chat",
            status="RESERVED",
            estimated_input_tokens=0,
            max_output_tokens=0,
            request_token_limit=0,
            daily_token_limit=0,
            monthly_token_limit=0,
            reserved_tokens=0,
            reserved_cost_usd=Decimal("0"),
            provider_transport_attempted=False,
        )
        db.add(reservation)
        await db.flush()
        return reservation

    async def _persist_answer(self, db, run, request, message, conversation, answer: AnalysisChatOutput) -> None:
        existing = (
            await db.execute(select(AIResultRecord).where(
                AIResultRecord.tenant_id == run.tenant_id,
                AIResultRecord.ai_request_id == request.id,
            ))
        ).scalar_one_or_none()
        if existing is None:
            existing = AIResultRecord(
                tenant_id=run.tenant_id,
                ai_request_id=request.id,
                status="COMPLETED",
                result_json=answer.model_dump(mode="json"),
                terminal_reason="STAGING_FAKE" if request.request_json.get("request_intent") == "FAKE_PROVIDER_CANARY" else "ANALYSIS_CHAT",
                completed_at=_now(),
            )
            db.add(existing)
            await db.flush()
        usage = (
            await db.execute(select(AIUsageRecord).where(
                AIUsageRecord.tenant_id == run.tenant_id,
                AIUsageRecord.ai_request_id == request.id,
            ))
        ).scalar_one_or_none()
        is_fake = request.request_json.get("request_intent") == "FAKE_PROVIDER_CANARY"
        no_provider_required = request.request_kind == "CHILD_ANALYSIS"
        zero_cost_turn = is_fake or no_provider_required
        if usage is None and zero_cost_turn:
            usage = AIUsageRecord(
                tenant_id=run.tenant_id,
                ai_request_id=request.id,
                provider=message.effective_provider or "fake",
                model=message.effective_model or "fake-analysis-v1",
                module="analysis_chat",
                tokens_input=0,
                tokens_output=0,
                estimated_cost=Decimal("0"),
                actual_cost=Decimal("0"),
                currency="USD",
                pricing_snapshot_version=(
                    "ZERO_COST_FAKE_STAGING" if is_fake else "NO_PROVIDER_REQUIRED"
                ),
            )
            db.add(usage)
        elif usage is None:
            raise RuntimeError("ANALYSIS_CHAT_PROVIDER_USAGE_MISSING")
        reservation = await self._ensure_reservation(db, run, request)
        if zero_cost_turn:
            reservation.status = "RECONCILED"
            reservation.actual_tokens = 0
            reservation.actual_cost_usd = Decimal("0")
            reservation.released_tokens = 0
            reservation.provider_transport_attempted = False
            reservation.terminal_reason = (
                "ZERO_COST_FAKE_RECONCILED" if is_fake else "NO_PROVIDER_REQUIRED_RECONCILED"
            )
            reservation.reconciled_at = _now()
        elif reservation.status != "RECONCILED" or not reservation.provider_transport_attempted:
            raise RuntimeError("ANALYSIS_CHAT_BUDGET_RECONCILIATION_MISSING")

        document = answer.model_dump(mode="json")
        first_completion = message.ai_result_id is None
        message.status = "COMPLETED"
        message.content = answer.answer
        message.content_hash = _sha(answer.answer)
        message.answer_type = answer.answer_type
        message.ai_result_id = existing.id
        message.evidence_refs_json = document["evidence_refs"]
        message.modules_consulted_json = document["modules_consulted"]
        message.warnings_json = document["warnings"]
        message.limitations_json = document["limitations"]
        message.suggested_questions_json = document["suggested_questions"]
        message.new_data_queried = answer.new_data_queried
        message.provider_transport_attempted = not zero_cost_turn
        message.tokens_input = int(usage.tokens_input)
        message.tokens_output = int(usage.tokens_output)
        message.cost_usd = Decimal(usage.actual_cost)
        message.completed_at = _now()
        message.lock_version = int(message.lock_version or 0) + 1
        for ref in answer.evidence_refs:
            try:
                evidence_id = UUID(str(ref.get("evidence_id")))
            except (TypeError, ValueError):
                continue
            evidence = await db.get(AIToolEvidenceRecord, evidence_id)
            if evidence is None or evidence.tenant_id != run.tenant_id:
                raise RuntimeError("ANALYSIS_CHAT_EVIDENCE_SCOPE_INVALID")
            source_run_id = conversation.parent_analysis_run_id
            if evidence.ai_request_id == request.id:
                source_run_id = run.id
            await db.execute(insert(AIAnalysisMessageEvidence).values(
                id=uuid.uuid4(),
                message_id=message.id,
                tenant_id=run.tenant_id,
                evidence_id=evidence.id,
                source_run_id=source_run_id,
                tool_call_id=evidence.tool_call_audit_id,
                relation_type=str(ref.get("source") or "FROZEN_ANALYSIS")[:40],
            ).on_conflict_do_nothing(
                index_elements=[
                    AIAnalysisMessageEvidence.message_id,
                    AIAnalysisMessageEvidence.evidence_id,
                    AIAnalysisMessageEvidence.relation_type,
                ]
            ))
        if first_completion:
            conversation.total_tokens_input = (
                int(conversation.total_tokens_input or 0) + int(usage.tokens_input)
            )
            conversation.total_tokens_output = (
                int(conversation.total_tokens_output or 0) + int(usage.tokens_output)
            )
            conversation.total_cost_usd = (
                Decimal(str(conversation.total_cost_usd or 0))
                + Decimal(usage.actual_cost)
            )
        conversation.updated_at = _now()
        conversation.lock_version = int(conversation.lock_version or 0) + 1

    async def _emit_tokens(self, db, run, request, message, content: str) -> None:
        chunks = [content[index:index + 240] for index in range(0, len(content), 240)] or [""]
        for index, chunk in enumerate(chunks):
            await db.execute(insert(AIGraphEvent).values(
                tenant_id=run.tenant_id,
                graph_run_id=run.id,
                event_key=f"{run.id}:token:{index}",
                event_type="TOKEN",
                node_name="invoke_provider",
                status="STREAMING",
                payload={
                    "conversation_id": str(request.conversation_id),
                    "message_id": str(message.id),
                    "chunk": chunk,
                    "coalesced": True,
                },
            ).on_conflict_do_nothing(
                index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]
            ))
from ...models.ai_provider_key import AIProviderKey
