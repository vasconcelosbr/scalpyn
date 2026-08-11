"""Durable node handler for the derived Analysis Chat graph."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import os
from typing import Any
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ...database import run_db_task
from ...models.ai_graph import AIGraphEvent, AIGraphRun
from ...models.analysis_chat import (
    AIAnalysisConversation,
    AIAnalysisMessage,
    AIAnalysisMessageEvidence,
)
from ...models.config_profile import ConfigProfile
from ...models.systemic_ai import (
    AIBudgetReservationRecord,
    AIDatasetSnapshotRecord,
    AIRequestRecord,
    AIResultRecord,
    AIToolEvidenceRecord,
    AIUsageRecord,
)
from ...schemas.analysis_chat import AnalysisChatOutput, AnalysisChatRuntimeConfig
from ..errors import GraphNodeExecutionError, ProviderBlockedError
from .state import ScalpynGraphState


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
            if request.authority != "ANALYSIS_ONLY" or run.authority != "ANALYSIS_ONLY":
                raise RuntimeError("ANALYSIS_CHAT_AUTHORITY_DENIED")
            if request.parent_analysis_run_id != conversation.parent_analysis_run_id:
                raise RuntimeError("ANALYSIS_CHAT_PARENT_LINK_MISMATCH")
            return {"authority": "ANALYSIS_ONLY"}
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
            proposal_id = uuid.uuid4()
            answer = AnalysisChatOutput(
                answer="Foi criado somente um draft governado. Nenhuma configuração live foi alterada.",
                answer_type="PROPOSAL",
                based_on="PROPOSAL_DRAFT",
                parent_analysis_run_id=conversation.parent_analysis_run_id,
                proposal={
                    "proposal_id": str(proposal_id),
                    "requested_change": str(request_json.get("question") or "")[:1000],
                    "status": "DRAFT",
                    "authority": "CANDIDATE_ONLY",
                    "live_write": False,
                },
                warnings=["GLOBAL_RISK_AND_STRATEGIES_VALIDATION_REQUIRED"],
                limitations=["DRAFT_NOT_APPLIED"],
            ).model_dump(mode="json")
            message.proposal_id = proposal_id
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
                    answer_type=answer_type,
                    based_on=based_on,
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
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_REAL_PROVIDER_CHECKPOINT_REQUIRED",
                "A separate provider, cost and one-turn staging checkpoint is required",
            )
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
        if usage is None:
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
                pricing_snapshot_version="ZERO_COST_FAKE_STAGING",
            )
            db.add(usage)
        reservation = await self._ensure_reservation(db, run, request)
        reservation.status = "RECONCILED"
        reservation.actual_tokens = 0
        reservation.actual_cost_usd = Decimal("0")
        reservation.released_tokens = 0
        reservation.provider_transport_attempted = False
        reservation.terminal_reason = "ZERO_COST_FAKE_RECONCILED"
        reservation.reconciled_at = _now()

        document = answer.model_dump(mode="json")
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
        message.provider_transport_attempted = False
        message.tokens_input = 0
        message.tokens_output = 0
        message.cost_usd = Decimal("0")
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
        conversation.total_tokens_input = int(conversation.total_tokens_input or 0)
        conversation.total_tokens_output = int(conversation.total_tokens_output or 0)
        conversation.total_cost_usd = Decimal(str(conversation.total_cost_usd or 0))
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
