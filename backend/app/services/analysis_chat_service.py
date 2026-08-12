"""Application service for derived, tenant-safe Intelligence Run conversations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.hashing import canonical_hash
from ..ai_orchestration.langgraph.config import get_langgraph_settings
from ..models.ai_graph import AIGraphRun
from ..models.analysis_chat import AIAnalysisConversation, AIAnalysisMessage
from ..models.config_profile import ConfigProfile
from ..models.systemic_ai import (
    AIBudgetPolicyRecord,
    AIBudgetReservationRecord,
    AIJobRecord,
    AIModelApprovalRecord,
    AIModelResolutionRecord,
    AIPromptVersion,
    AIRequestRecord,
    AIResultRecord,
)
from ..schemas.analysis_chat import (
    AnalysisChatDataMode,
    AnalysisChatRequestKind,
    AnalysisChatRuntimeConfig,
)
from .ai_graph_service import AIGraphRunService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AnalysisChatError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class AnalysisChatService:
    @staticmethod
    async def runtime_config(db: AsyncSession, tenant_id: UUID) -> AnalysisChatRuntimeConfig:
        record = (
            await db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.user_id == tenant_id,
                    ConfigProfile.pool_id.is_(None),
                    ConfigProfile.config_type == "ai_analysis_chat_runtime",
                    ConfigProfile.is_active.is_(True),
                ).order_by(ConfigProfile.updated_at.desc()).limit(1)
            )
        ).scalar_one_or_none()
        try:
            return AnalysisChatRuntimeConfig.model_validate(record.config_json if record else {})
        except Exception as exc:
            raise AnalysisChatError("ANALYSIS_CHAT_RUNTIME_CONFIG_INVALID") from exc

    @staticmethod
    def _require_mode(config: AnalysisChatRuntimeConfig, mode: AnalysisChatDataMode) -> None:
        if not config.enabled:
            raise AnalysisChatError("ANALYSIS_CHAT_DISABLED", status_code=403)
        allowed = {
            AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY: True,
            AnalysisChatDataMode.ALLOW_READONLY_REFRESH: config.readonly_refresh_enabled,
            AnalysisChatDataMode.CREATE_CHILD_ANALYSIS: config.child_analysis_enabled,
            AnalysisChatDataMode.DRAFT_PROPOSAL: (
                config.proposals_enabled and config.governed_actions_enabled
            ),
        }[mode]
        if not allowed:
            raise AnalysisChatError(f"ANALYSIS_CHAT_MODE_DISABLED:{mode.value}", status_code=403)

    @staticmethod
    async def create_conversation(
        db: AsyncSession, *, tenant_id: UUID, user_id: UUID, run_id: UUID, title: str | None,
    ) -> AIAnalysisConversation:
        config = await AnalysisChatService.runtime_config(db, tenant_id)
        if not config.enabled:
            raise AnalysisChatError("ANALYSIS_CHAT_DISABLED", status_code=403)
        row = (
            await db.execute(
                select(AIGraphRun, AIResultRecord)
                .join(AIResultRecord, AIResultRecord.ai_request_id == AIGraphRun.ai_request_id)
                .where(AIGraphRun.id == run_id, AIGraphRun.tenant_id == tenant_id)
            )
        ).one_or_none()
        if row is None:
            raise AnalysisChatError("ANALYSIS_CHAT_PARENT_RUN_NOT_FOUND", status_code=404)
        run, result = row
        if run.status != "COMPLETED" or result.status != "COMPLETED":
            raise AnalysisChatError("ANALYSIS_CHAT_PARENT_RUN_NOT_ELIGIBLE")
        conversation_id = uuid.uuid4()
        conversation = AIAnalysisConversation(
            id=conversation_id,
            tenant_id=tenant_id,
            parent_analysis_run_id=run.id,
            parent_result_id=result.id,
            thread_id=f"analysis-chat:{conversation_id}",
            title=(title or "Conversa sobre a análise").strip()[:200],
            status="ACTIVE",
            created_by_user_id=user_id,
        )
        db.add(conversation)
        await db.flush()
        return conversation

    @staticmethod
    async def get_conversation(
        db: AsyncSession, *, tenant_id: UUID, conversation_id: UUID, lock: bool = False,
    ) -> AIAnalysisConversation:
        query = select(AIAnalysisConversation).where(
            AIAnalysisConversation.id == conversation_id,
            AIAnalysisConversation.tenant_id == tenant_id,
        )
        if lock:
            query = query.with_for_update()
        conversation = (await db.execute(query)).scalar_one_or_none()
        if conversation is None:
            raise AnalysisChatError("ANALYSIS_CHAT_CONVERSATION_NOT_FOUND", status_code=404)
        return conversation

    @staticmethod
    async def list_conversations(
        db: AsyncSession, *, tenant_id: UUID, run_id: UUID,
    ) -> tuple[AnalysisChatRuntimeConfig, list[AIAnalysisConversation]]:
        config = await AnalysisChatService.runtime_config(db, tenant_id)
        parent = await db.get(AIGraphRun, run_id)
        if parent is None or parent.tenant_id != tenant_id:
            raise AnalysisChatError("ANALYSIS_CHAT_PARENT_RUN_NOT_FOUND", status_code=404)
        rows = list((await db.execute(
            select(AIAnalysisConversation).where(
                AIAnalysisConversation.tenant_id == tenant_id,
                AIAnalysisConversation.parent_analysis_run_id == run_id,
            ).order_by(AIAnalysisConversation.updated_at.desc())
        )).scalars().all())
        return config, rows

    @staticmethod
    async def list_messages(
        db: AsyncSession, *, tenant_id: UUID, conversation_id: UUID,
    ) -> list[AIAnalysisMessage]:
        await AnalysisChatService.get_conversation(
            db, tenant_id=tenant_id, conversation_id=conversation_id
        )
        return list((await db.execute(
            select(AIAnalysisMessage).where(
                AIAnalysisMessage.tenant_id == tenant_id,
                AIAnalysisMessage.conversation_id == conversation_id,
            ).order_by(AIAnalysisMessage.sequence_number)
        )).scalars().all())

    @staticmethod
    def _intent() -> str:
        environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower()
        fake_enabled = os.getenv("LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "false").lower() == "true"
        return "FAKE_PROVIDER_CANARY" if "staging" in environment and fake_enabled else "NORMAL_ANALYSIS"

    @staticmethod
    def _request_kind(mode: AnalysisChatDataMode) -> AnalysisChatRequestKind:
        if mode is AnalysisChatDataMode.CREATE_CHILD_ANALYSIS:
            return AnalysisChatRequestKind.CHILD_ANALYSIS
        if mode is AnalysisChatDataMode.DRAFT_PROPOSAL:
            return AnalysisChatRequestKind.PROPOSAL_DRAFT
        return AnalysisChatRequestKind.FOLLOW_UP_CHAT

    @staticmethod
    async def send_message(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        message: str,
        data_mode: AnalysisChatDataMode,
        idempotency_key: str,
        response_language: str,
    ) -> tuple[AIAnalysisMessage, AIAnalysisMessage, AIGraphRun, bool]:
        config = await AnalysisChatService.runtime_config(db, tenant_id)
        AnalysisChatService._require_mode(config, data_mode)
        intent = AnalysisChatService._intent()
        provider_required = data_mode in {
            AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY,
            AnalysisChatDataMode.ALLOW_READONLY_REFRESH,
            AnalysisChatDataMode.DRAFT_PROPOSAL,
        }
        if intent == "NORMAL_ANALYSIS" and provider_required and (
            config.provider_max_cost_usd <= 0
            or config.request_token_limit <= 0
            or config.daily_token_limit < config.request_token_limit
            or config.monthly_token_limit < config.daily_token_limit
        ):
            raise AnalysisChatError("ANALYSIS_CHAT_PROVIDER_BUDGET_NOT_CONFIGURED")
        normalized = message.strip()
        if not normalized:
            raise AnalysisChatError("ANALYSIS_CHAT_MESSAGE_EMPTY", status_code=422)
        if len(normalized) > config.max_message_characters:
            raise AnalysisChatError("ANALYSIS_CHAT_MESSAGE_TOO_LARGE", status_code=422)

        conversation = await AnalysisChatService.get_conversation(
            db, tenant_id=tenant_id, conversation_id=conversation_id, lock=True
        )
        if conversation.status != "ACTIVE":
            raise AnalysisChatError("ANALYSIS_CHAT_CONVERSATION_NOT_ACTIVE")
        existing = (
            await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.tenant_id == tenant_id,
                AIAnalysisMessage.conversation_id == conversation_id,
                AIAnalysisMessage.idempotency_key == idempotency_key,
                AIAnalysisMessage.role == "USER",
            ))
        ).scalar_one_or_none()
        if existing is not None:
            assistant = (
                await db.execute(select(AIAnalysisMessage).where(
                    AIAnalysisMessage.parent_message_id == existing.id,
                    AIAnalysisMessage.tenant_id == tenant_id,
                ))
            ).scalar_one()
            graph_run = await db.get(AIGraphRun, assistant.graph_run_id)
            if graph_run is None or graph_run.tenant_id != tenant_id:
                raise AnalysisChatError("ANALYSIS_CHAT_GRAPH_LINK_INVALID")
            return existing, assistant, graph_run, True

        active = (
            await db.execute(select(AIAnalysisMessage.id).where(
                AIAnalysisMessage.tenant_id == tenant_id,
                AIAnalysisMessage.conversation_id == conversation_id,
                AIAnalysisMessage.role == "ASSISTANT",
                AIAnalysisMessage.status.in_(("PENDING", "QUEUED", "STREAMING", "INTERRUPTED")),
            ).limit(1))
        ).scalar_one_or_none()
        if active is not None:
            raise AnalysisChatError("ANALYSIS_CHAT_MESSAGE_ALREADY_ACTIVE", status_code=429)

        parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
        parent_result = await db.get(AIResultRecord, conversation.parent_result_id)
        if (
            parent_run is None or parent_result is None
            or parent_run.tenant_id != tenant_id or parent_result.tenant_id != tenant_id
            or parent_run.status != "COMPLETED" or parent_result.status != "COMPLETED"
        ):
            raise AnalysisChatError("ANALYSIS_CHAT_PARENT_CONTRACT_INVALID")
        parent_request = await db.get(AIRequestRecord, parent_run.ai_request_id)
        parent_resolution = await db.get(AIModelResolutionRecord, parent_request.model_resolution_id) if parent_request else None
        if parent_request is None or parent_resolution is None or parent_request.tenant_id != tenant_id:
            raise AnalysisChatError("ANALYSIS_CHAT_PARENT_LINEAGE_INVALID")

        sequence = int(conversation.message_count or 0) + 1
        request_kind = AnalysisChatService._request_kind(data_mode)
        user_message = AIAnalysisMessage(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sequence_number=sequence,
            role="USER",
            message_type="USER_QUESTION",
            status="COMPLETED",
            content=normalized,
            content_hash=_sha(normalized),
            idempotency_key=idempotency_key,
            request_kind=request_kind.value,
            data_mode=data_mode.value,
            created_by_user_id=user_id,
            completed_at=_now(),
        )
        db.add(user_message)
        await db.flush()

        prompt_key = (
            "analysis-chat-governed-change"
            if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL
            else "analysis-chat-system"
        )
        prompt = (
            await db.execute(select(AIPromptVersion).where(
                AIPromptVersion.prompt_key == prompt_key,
                AIPromptVersion.status == "APPROVED",
            ).order_by(AIPromptVersion.approved_at.desc(), AIPromptVersion.semantic_version.desc()).limit(1))
        ).scalar_one_or_none()
        if prompt is None:
            raise AnalysisChatError("ANALYSIS_CHAT_PROMPT_NOT_DEPLOYED")

        if intent == "FAKE_PROVIDER_CANARY":
            configured_provider = effective_provider = "fake"
            configured_model = effective_model = "fake-analysis-v1"
            capabilities = ["text", "structured_output", "staging_fake"]
            resolution_reason = "STAGING_FAKE_PROVIDER_CANARY"
        else:
            configured_provider = parent_resolution.configured_provider or parent_resolution.effective_provider
            configured_model = parent_resolution.configured_model or parent_resolution.effective_model
            effective_provider = parent_resolution.effective_provider
            effective_model = parent_resolution.effective_model
            capabilities = list(parent_resolution.capabilities or [])
            resolution_reason = "ANALYSIS_CHAT_PARENT_MODEL_POLICY"
        resolution = AIModelResolutionRecord(
            tenant_id=tenant_id,
            requested_provider=configured_provider,
            requested_model=configured_model,
            configured_provider=configured_provider,
            configured_model=configured_model,
            effective_provider=effective_provider,
            effective_model=effective_model,
            catalog_snapshot_hash=_sha(f"{effective_provider}:{effective_model}:analysis-chat-v1"),
            capabilities=capabilities,
            resolution_policy_version="analysis-chat-model-policy-v1",
            resolution_reason=resolution_reason,
        )
        db.add(resolution)
        await db.flush()

        approval = None
        budget = None
        if intent == "NORMAL_ANALYSIS" and provider_required:
            parent_frozen = dict(parent_request.request_json or {}).get("frozen_context") or {}
            try:
                parent_approval_id = UUID(str(parent_frozen["model_approval_id"]))
            except (KeyError, ValueError) as exc:
                raise AnalysisChatError("ANALYSIS_CHAT_PARENT_APPROVAL_MISSING") from exc
            parent_approval = await db.get(AIModelApprovalRecord, parent_approval_id)
            if (
                parent_approval is None
                or parent_approval.tenant_id != tenant_id
                or parent_approval.provider != effective_provider
                or parent_approval.model != effective_model
                or parent_approval.status != "APPROVED"
            ):
                raise AnalysisChatError("ANALYSIS_CHAT_PARENT_APPROVAL_INVALID")
            now = _now()
            approval_id = uuid.uuid4()
            approval_payload = {
                "id": str(approval_id),
                "tenant_id": str(tenant_id),
                "provider": effective_provider,
                "model": effective_model,
                "max_cost_usd": str(config.provider_max_cost_usd),
                "scope": "ANALYSIS_CHAT_TURN",
                "approved_by": str(user_id),
                "approved_at": now.isoformat(),
                "parent_analysis_run_id": str(parent_run.id),
                "user_message_id": str(user_message.id),
            }
            approval = AIModelApprovalRecord(
                id=approval_id,
                tenant_id=tenant_id,
                provider=effective_provider,
                model=effective_model,
                max_cost_usd=config.provider_max_cost_usd,
                input_cost_per_million=parent_approval.input_cost_per_million,
                output_cost_per_million=parent_approval.output_cost_per_million,
                max_output_tokens=parent_approval.max_output_tokens,
                pricing_source_url=parent_approval.pricing_source_url,
                pricing_observed_at=parent_approval.pricing_observed_at,
                pricing_snapshot_hash=parent_approval.pricing_snapshot_hash,
                approval_phrase_hash=canonical_hash({
                    "action": "AUTHENTICATED_ANALYSIS_CHAT_SEND",
                    "user_message_id": str(user_message.id),
                    "max_cost_usd": str(config.provider_max_cost_usd),
                }),
                approval_method="ANALYSIS_CHAT_SEND_ACTION",
                analysis_profile_id=parent_approval.analysis_profile_id,
                scope="ANALYSIS_CHAT_TURN",
                status="APPROVED",
                approved_by=user_id,
                approved_at=now,
                expires_at=now + timedelta(seconds=get_langgraph_settings().model_approval_ttl_seconds),
                content_hash=canonical_hash(approval_payload),
            )
            db.add(approval)
            budget = (
                await db.execute(select(AIBudgetPolicyRecord).where(
                    AIBudgetPolicyRecord.tenant_id == tenant_id,
                    AIBudgetPolicyRecord.provider == effective_provider,
                    AIBudgetPolicyRecord.model == effective_model,
                    AIBudgetPolicyRecord.module == "analysis_chat",
                ).with_for_update())
            ).scalar_one_or_none()
            if budget is None:
                budget = AIBudgetPolicyRecord(
                    tenant_id=tenant_id,
                    provider=effective_provider,
                    model=effective_model,
                    module="analysis_chat",
                )
                db.add(budget)
            budget.request_token_limit = config.request_token_limit
            budget.daily_token_limit = config.daily_token_limit
            budget.monthly_token_limit = config.monthly_token_limit
            budget.null_limit_policy = "DENY"
            budget.is_active = True
            await db.flush()

        correlation = f"analysis-chat:{conversation.id}:{idempotency_key}"[:160]
        request = AIRequestRecord(
            tenant_id=tenant_id,
            requested_by_user_id=user_id,
            origin_module="intelligence_runs",
            origin_view=f"analysis-chat:{conversation.id}",
            analysis_mode="FOLLOW_UP_CHAT",
            authority=(
                "PROPOSAL_ONLY"
                if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL
                else "ANALYSIS_ONLY"
            ),
            question_hash=_sha(normalized),
            correlation_id=correlation,
            model_resolution_id=resolution.id,
            prompt_version_id=prompt.id,
            dataset_snapshot_id=parent_request.dataset_snapshot_id,
            configuration_bundle_id=parent_request.configuration_bundle_id,
            request_json={
                "request_intent": intent,
                "request_kind": request_kind.value,
                "data_mode": data_mode.value,
                "question": normalized,
                "response_language": response_language[:16],
                "model_approval_id": str(approval.id) if approval else None,
                "provider_max_cost_usd": str(config.provider_max_cost_usd),
                "trust_labels": {
                    "question": "USER_INPUT",
                    "parent_result": "TRUSTED_CALCULATED",
                    "evidence": "DATABASE_UNTRUSTED_TEXT",
                },
                "tool_allowlist": (
                    ["market_regime.get_current"]
                    if data_mode is AnalysisChatDataMode.ALLOW_READONLY_REFRESH
                    else [
                        "global_risk.validate_recommendation",
                        "strategies.validate_recommendation",
                    ] if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL else []
                ),
            },
            request_kind=request_kind.value,
            conversation_id=conversation.id,
            message_id=user_message.id,
            parent_analysis_run_id=parent_run.id,
        )
        db.add(request)
        await db.flush()
        # The turn becomes budget-auditable at acceptance time, including
        # human-gated turns that may remain interrupted without provider use.
        db.add(AIBudgetReservationRecord(
            tenant_id=tenant_id,
            ai_request_id=request.id,
            budget_policy_id=budget.id if budget else None,
            model_approval_id=approval.id if approval else None,
            request_intent=intent,
            provider=effective_provider,
            model=effective_model,
            module="analysis_chat",
            status="RESERVED",
            estimated_input_tokens=0,
            max_output_tokens=int(approval.max_output_tokens) if approval else 0,
            request_token_limit=config.request_token_limit if approval else 0,
            daily_token_limit=config.daily_token_limit if approval else 0,
            monthly_token_limit=config.monthly_token_limit if approval else 0,
            reserved_tokens=0,
            reserved_cost_usd=Decimal("0"),
            provider_transport_attempted=False,
        ))
        user_message.ai_request_id = request.id
        user_message.prompt_version_id = prompt.id

        job = AIJobRecord(
            tenant_id=tenant_id,
            ai_request_id=request.id,
            purpose="analysis_chat",
            dedupe_key=_sha(f"{conversation.id}:{idempotency_key}"),
            status="QUEUED",
            max_attempts=1,
        )
        db.add(job)
        await db.flush()
        graph_run = await AIGraphRunService.create(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            graph_key="analysis-chat-v1",
            ai_request_id=request.id,
            idempotency_key=f"analysis-chat:{conversation.id}:{idempotency_key}"[:160],
        )
        # ``ai_graph_runs.thread_id`` is unique per durable execution.  Keep the
        # canonical conversation thread on ``ai_analysis_conversations`` and
        # derive a collision-free checkpoint thread for each turn.
        graph_run.thread_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{conversation.thread_id}:message:{user_message.id}",
        )
        graph_run.ai_job_id = job.id

        assistant = AIAnalysisMessage(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            sequence_number=sequence + 1,
            role="ASSISTANT",
            message_type=(
                "PROPOSAL_CARD" if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL
                else "CHILD_ANALYSIS_NOTICE" if data_mode is AnalysisChatDataMode.CREATE_CHILD_ANALYSIS
                else "ASSISTANT_ANSWER"
            ),
            status="QUEUED",
            parent_message_id=user_message.id,
            request_kind=request_kind.value,
            data_mode=data_mode.value,
            ai_request_id=request.id,
            graph_run_id=graph_run.id,
            configured_provider=configured_provider,
            configured_model=configured_model,
            effective_provider=effective_provider,
            effective_model=effective_model,
            prompt_version_id=prompt.id,
            created_by_user_id=user_id,
        )
        db.add(assistant)
        conversation.message_count = sequence + 1
        conversation.lock_version = int(conversation.lock_version or 0) + 1
        conversation.last_message_at = _now()
        conversation.updated_at = conversation.last_message_at
        await db.flush()
        return user_message, assistant, graph_run, False

    @staticmethod
    async def cancel(
        db: AsyncSession, *, tenant_id: UUID, user_id: UUID, conversation_id: UUID,
    ) -> AIAnalysisMessage | None:
        conversation = await AnalysisChatService.get_conversation(
            db, tenant_id=tenant_id, conversation_id=conversation_id, lock=True
        )
        message = (
            await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.tenant_id == tenant_id,
                AIAnalysisMessage.conversation_id == conversation.id,
                AIAnalysisMessage.role == "ASSISTANT",
                AIAnalysisMessage.status.in_(("PENDING", "QUEUED", "STREAMING", "INTERRUPTED")),
            ).order_by(AIAnalysisMessage.sequence_number.desc()).limit(1).with_for_update())
        ).scalar_one_or_none()
        if message is None:
            return None
        now = _now()
        message.status = "CANCELLED"
        message.cancelled_at = now
        message.provider_transport_attempted = False
        if message.ai_request_id:
            reservation = (await db.execute(select(AIBudgetReservationRecord).where(
                AIBudgetReservationRecord.tenant_id == tenant_id,
                AIBudgetReservationRecord.ai_request_id == message.ai_request_id,
            ).with_for_update())).scalar_one_or_none()
            if reservation is not None and reservation.status in {"RESERVED", "TRANSPORT_STARTED"}:
                reservation.status = "RELEASED"
                reservation.released_tokens = int(reservation.reserved_tokens or 0)
                reservation.provider_transport_attempted = False
                reservation.terminal_reason = "CANCELLED_BY_AUTHORIZED_ACTOR"
                reservation.released_at = now
                reservation.updated_at = now
            job = (await db.execute(select(AIJobRecord).where(
                AIJobRecord.tenant_id == tenant_id,
                AIJobRecord.ai_request_id == message.ai_request_id,
            ).with_for_update())).scalar_one_or_none()
            if job is not None and job.status not in {"COMPLETED", "FAILED_TERMINAL", "CANCELLED"}:
                job.status = "CANCELLED"
                job.completed_at = now
                job.terminal_reason = "CANCELLED_BY_AUTHORIZED_ACTOR"
                job.lease_owner = None
                job.lease_expires_at = None
        if message.graph_run_id:
            run = await db.get(AIGraphRun, message.graph_run_id)
            if run and run.tenant_id == tenant_id and run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
                run.status = "CANCELLED"
                run.cancelled_at = now
                run.completed_at = now
                run.terminal_reason = "CANCELLED_BY_AUTHORIZED_ACTOR"
        conversation.updated_at = now
        conversation.lock_version = int(conversation.lock_version or 0) + 1
        return message
