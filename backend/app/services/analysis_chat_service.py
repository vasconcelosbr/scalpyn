"""Application service for derived, tenant-safe Intelligence Run conversations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import os
import re
import uuid
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.hashing import canonical_hash
from ..ai_orchestration.langgraph.config import get_langgraph_settings
from ..models.ai_graph import (
    AIGraphDefinition,
    AIGraphEvent,
    AIGraphInterrupt,
    AIGraphRun,
)
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
    AIUsageRecord,
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


def _cancel_budget_reservation(
    reservation: AIBudgetReservationRecord | None,
    *,
    now: datetime,
) -> bool:
    """Apply cancellation without erasing evidence of provider transport."""
    if reservation is None:
        return False
    transport_attempted = bool(
        reservation.provider_transport_attempted
        or reservation.status in {"TRANSPORT_STARTED", "TRANSPORT_ERROR"}
        or int(getattr(reservation, "actual_tokens", 0) or 0) > 0
        or Decimal(str(getattr(reservation, "actual_cost_usd", 0) or 0)) > 0
    )
    if reservation.status == "RESERVED" and not transport_attempted:
        reservation.status = "RELEASED"
        reservation.actual_tokens = 0
        reservation.actual_cost_usd = Decimal("0")
        reservation.released_tokens = int(reservation.reserved_tokens or 0)
        reservation.provider_transport_attempted = False
        reservation.terminal_reason = "CANCELLED_BEFORE_PROVIDER_TRANSPORT"
        reservation.released_at = now
        reservation.updated_at = now
    elif reservation.status == "RESERVED":
        reservation.status = "TRANSPORT_ERROR"
        reservation.provider_transport_attempted = True
        reservation.terminal_reason = "CANCELLED_WITH_PROVIDER_TRANSPORT_AUDIT_CONFLICT"
        reservation.updated_at = now
    elif reservation.status == "TRANSPORT_STARTED":
        # The request may still be in flight. Keep it reconcilable and never
        # claim that its reservation was released before usage is known.
        reservation.provider_transport_attempted = True
        reservation.terminal_reason = "CANCELLED_AFTER_PROVIDER_TRANSPORT_STARTED"
        reservation.updated_at = now
    return transport_attempted


class AnalysisChatError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 409):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class AnalysisChatService:
    _GOVERNED_ACTION_PREFIX = re.compile(
        r"^(?:por favor,?\s+)?"
        r"(?:(?:preciso|quero)\s+que\s+(?:voc[eê]\s+)?|pode\s+(?:voc[eê]\s+)?)?"
        r"(?:realiz(?:ar|e)|fa[cç]a|aplic(?:ar|e)|execut(?:ar|e)|alter(?:ar|e)|"
        r"ajust(?:ar|e)|modific(?:ar|e)|atualiz(?:ar|e)|corr(?:igir|ija)|"
        r"exclu(?:ir|a)|delet(?:ar|e)|remov(?:er|a)|desativ(?:ar|e)|"
        r"ativ(?:ar|e)|cri(?:ar|e)|implement(?:ar|e))\b",
        re.IGNORECASE,
    )
    _GOVERNED_ACTION_TARGETS = (
        "perfi", "configura", "score", "estrat", "regra", "threshold",
        "limite", "peso", "parâmetro", "parametro", "filtro", "sinal",
        "risco", "risk",
    )

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
    async def _canonical_parent(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        run: AIGraphRun,
        result: AIResultRecord,
    ) -> tuple[AIGraphRun, AIResultRecord]:
        """Resolve chat-on-chat selections back to the original analysis run."""
        for _ in range(8):
            definition = await db.get(AIGraphDefinition, run.graph_definition_id)
            if definition is None:
                raise AnalysisChatError("ANALYSIS_CHAT_GRAPH_DEFINITION_MISSING")
            if definition.graph_key != "analysis-chat-v1":
                return run, result
            request = await db.get(AIRequestRecord, run.ai_request_id)
            if (
                request is None
                or request.tenant_id != tenant_id
                or request.parent_analysis_run_id is None
            ):
                raise AnalysisChatError("ANALYSIS_CHAT_CANONICAL_PARENT_MISSING")
            parent_run = await db.get(AIGraphRun, request.parent_analysis_run_id)
            if parent_run is None or parent_run.tenant_id != tenant_id:
                raise AnalysisChatError("ANALYSIS_CHAT_CANONICAL_PARENT_INVALID")
            parent_result = (
                await db.execute(select(AIResultRecord).where(
                    AIResultRecord.tenant_id == tenant_id,
                    AIResultRecord.ai_request_id == parent_run.ai_request_id,
                ))
            ).scalar_one_or_none()
            if parent_result is None:
                raise AnalysisChatError("ANALYSIS_CHAT_CANONICAL_RESULT_MISSING")
            run, result = parent_run, parent_result
        raise AnalysisChatError("ANALYSIS_CHAT_PARENT_NESTING_LIMIT_EXCEEDED")

    @staticmethod
    async def refresh_proposal_confirmation_contract(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        user_id: UUID,
        message: AIAnalysisMessage,
        interrupt_id: UUID,
        decision: str,
    ) -> None:
        """Refresh a still-untransported proposal turn at its first human gate.

        Older turns inherited the parent analysis output allowance, which is
        too small for a typed multi-profile proposal.  Reissuing the per-turn
        approval here keeps the larger allowance bound to the explicit human
        confirmation and leaves an immutable graph event for audit.
        """
        if decision != "approve" or message.data_mode != AnalysisChatDataMode.DRAFT_PROPOSAL.value:
            return
        if message.graph_run_id is None or message.ai_request_id is None:
            raise AnalysisChatError("ANALYSIS_CHAT_PROPOSAL_CONTRACT_INVALID")
        # Keep the same lock order used by ``AIGraphRunService.resume`` so
        # concurrent decisions cannot deadlock (run -> interrupt) or mint two
        # proposal approvals for the same human gate.
        locked_run = (
            await db.execute(
                select(AIGraphRun)
                .where(
                    AIGraphRun.id == message.graph_run_id,
                    AIGraphRun.tenant_id == tenant_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        interrupt = (
            await db.execute(
                select(AIGraphInterrupt)
                .where(AIGraphInterrupt.id == interrupt_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            locked_run is None
            or interrupt is None
            or interrupt.tenant_id != tenant_id
            or interrupt.graph_run_id != message.graph_run_id
            or interrupt.interrupt_type != "PROPOSAL_CONFIRMATION"
            or interrupt.status != "PENDING"
        ):
            return
        request = await db.get(AIRequestRecord, message.ai_request_id)
        reservation = (
            await db.execute(select(AIBudgetReservationRecord).where(
                AIBudgetReservationRecord.tenant_id == tenant_id,
                AIBudgetReservationRecord.ai_request_id == message.ai_request_id,
            ).with_for_update())
        ).scalar_one_or_none()
        if request is None or request.tenant_id != tenant_id or reservation is None:
            raise AnalysisChatError("ANALYSIS_CHAT_PROPOSAL_CONTRACT_INVALID")
        if reservation.status != "RESERVED" or reservation.provider_transport_attempted:
            raise AnalysisChatError("ANALYSIS_CHAT_PROPOSAL_CONTRACT_ALREADY_USED")

        config = await AnalysisChatService.runtime_config(db, tenant_id)
        latest_prompt = (
            await db.execute(select(AIPromptVersion).where(
                AIPromptVersion.prompt_key == "analysis-chat-governed-change",
                AIPromptVersion.status == "APPROVED",
            ).order_by(
                AIPromptVersion.approved_at.desc(),
                AIPromptVersion.semantic_version.desc(),
            ).limit(1))
        ).scalar_one_or_none()
        request_json = dict(request.request_json or {})
        # Fake-provider canaries intentionally have no real-provider approval.
        # Their proposal path must remain usable for transport-free staging
        # verification and must never mint a production provider allowance.
        if request_json.get("request_intent") != "NORMAL_ANALYSIS":
            return
        try:
            current_approval_id = UUID(str(request_json["model_approval_id"]))
        except (KeyError, ValueError) as exc:
            raise AnalysisChatError("ANALYSIS_CHAT_MODEL_APPROVAL_REQUIRED") from exc
        current_approval = await db.get(AIModelApprovalRecord, current_approval_id)
        if (
            current_approval is None
            or current_approval.tenant_id != tenant_id
            or current_approval.scope != "ANALYSIS_CHAT_TURN"
            or current_approval.status != "APPROVED"
        ):
            raise AnalysisChatError("ANALYSIS_CHAT_MODEL_APPROVAL_INVALID")
        if latest_prompt is None:
            raise AnalysisChatError("ANALYSIS_CHAT_PROMPT_NOT_DEPLOYED")

        old_prompt_id = request.prompt_version_id
        request.prompt_version_id = latest_prompt.id
        message.prompt_version_id = latest_prompt.id
        desired_output_tokens = max(
            int(config.proposal_max_output_tokens),
            int(current_approval.max_output_tokens),
        )
        # The first human gate may remain open beyond the original per-turn
        # TTL. Always mint a fresh immutable approval while the interrupt is
        # locked and still PENDING. Reusing an old approval would make a valid
        # confirmation fail solely because the operator reviewed it carefully.
        now = _now()
        replacement_approval_id = uuid.uuid4()
        approval_payload = {
            "id": str(replacement_approval_id),
            "tenant_id": str(tenant_id),
            "provider": current_approval.provider,
            "model": current_approval.model,
            "max_cost_usd": str(current_approval.max_cost_usd),
            "max_output_tokens": desired_output_tokens,
            "scope": "ANALYSIS_CHAT_TURN",
            "approved_by": str(user_id),
            "approved_at": now.isoformat(),
            "replaces_approval_id": str(current_approval.id),
            "human_gate": "PROPOSAL_CONFIRMATION",
        }
        replacement = AIModelApprovalRecord(
            id=replacement_approval_id,
            tenant_id=tenant_id,
            provider=current_approval.provider,
            model=current_approval.model,
            max_cost_usd=current_approval.max_cost_usd,
            input_cost_per_million=current_approval.input_cost_per_million,
            output_cost_per_million=current_approval.output_cost_per_million,
            max_output_tokens=desired_output_tokens,
            pricing_source_url=current_approval.pricing_source_url,
            pricing_observed_at=current_approval.pricing_observed_at,
            pricing_snapshot_hash=current_approval.pricing_snapshot_hash,
            approval_phrase_hash=canonical_hash({
                "action": "CONFIRM_GOVERNED_PROPOSAL_GENERATION",
                "message_id": str(message.id),
                "interrupt_id": str(interrupt_id),
                "max_output_tokens": desired_output_tokens,
            }),
            approval_method="ANALYSIS_CHAT_PROPOSAL_CONFIRMATION",
            analysis_profile_id=current_approval.analysis_profile_id,
            scope="ANALYSIS_CHAT_TURN",
            status="APPROVED",
            approved_by=user_id,
            approved_at=now,
            expires_at=now + timedelta(
                seconds=get_langgraph_settings().model_approval_ttl_seconds
            ),
            content_hash=canonical_hash(approval_payload),
        )
        db.add(replacement)
        request_json["model_approval_id"] = str(replacement_approval_id)
        request.request_json = request_json
        reservation.model_approval_id = replacement_approval_id
        reservation.max_output_tokens = desired_output_tokens

        db.add(AIGraphEvent(
            tenant_id=tenant_id,
            graph_run_id=message.graph_run_id,
            event_key=f"{message.graph_run_id}:proposal-contract:{interrupt_id}",
            event_type="PROPOSAL_CONTRACT_REFRESHED",
            status="APPROVED",
            payload={
                "interrupt_id": str(interrupt_id),
                "old_prompt_version_id": str(old_prompt_id),
                "prompt_version_id": str(latest_prompt.id),
                "old_model_approval_id": str(current_approval.id),
                "model_approval_id": str(replacement_approval_id),
                "max_output_tokens": desired_output_tokens,
            },
        ))
        await db.flush()

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
        run, result = await AnalysisChatService._canonical_parent(
            db, tenant_id=tenant_id, run=run, result=result
        )
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
        parent_result = (
            await db.execute(select(AIResultRecord).where(
                AIResultRecord.tenant_id == tenant_id,
                AIResultRecord.ai_request_id == parent.ai_request_id,
            ))
        ).scalar_one_or_none()
        if parent_result is None:
            raise AnalysisChatError("ANALYSIS_CHAT_PARENT_CONTRACT_INVALID")
        parent, _ = await AnalysisChatService._canonical_parent(
            db, tenant_id=tenant_id, run=parent, result=parent_result
        )
        rows = list((await db.execute(
            select(AIAnalysisConversation).where(
                AIAnalysisConversation.tenant_id == tenant_id,
                AIAnalysisConversation.parent_analysis_run_id == parent.id,
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
    def _resolve_data_mode(
        config: AnalysisChatRuntimeConfig,
        requested_mode: AnalysisChatDataMode,
        message: str,
    ) -> AnalysisChatDataMode:
        if requested_mode is not AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY:
            return requested_mode
        governed_enabled = (
            config.proposals_enabled
            and config.governed_actions_enabled
            and config.live_config_write_enabled
        )
        lowered = message.casefold()
        if (
            governed_enabled
            and AnalysisChatService._GOVERNED_ACTION_PREFIX.search(message)
            and any(target in lowered for target in AnalysisChatService._GOVERNED_ACTION_TARGETS)
        ):
            return AnalysisChatDataMode.DRAFT_PROPOSAL
        return requested_mode

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
        normalized = message.strip()
        if not normalized:
            raise AnalysisChatError("ANALYSIS_CHAT_MESSAGE_EMPTY", status_code=422)
        data_mode = AnalysisChatService._resolve_data_mode(
            config, data_mode, normalized
        )
        AnalysisChatService._require_mode(config, data_mode)
        intent = AnalysisChatService._intent()
        provider_required = data_mode in {
            AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY,
            AnalysisChatDataMode.ALLOW_READONLY_REFRESH,
            AnalysisChatDataMode.DRAFT_PROPOSAL,
        }
        if (
            config.budget_enforcement_enabled
            and intent == "NORMAL_ANALYSIS"
            and provider_required
            and (
                config.provider_max_cost_usd <= 0
                or config.request_token_limit <= 0
                or config.daily_token_limit < config.request_token_limit
                or config.monthly_token_limit < config.daily_token_limit
            )
        ):
            raise AnalysisChatError("ANALYSIS_CHAT_PROVIDER_BUDGET_NOT_CONFIGURED")
        if len(normalized) > config.max_message_characters:
            raise AnalysisChatError("ANALYSIS_CHAT_MESSAGE_TOO_LARGE", status_code=422)

        conversation = await AnalysisChatService.get_conversation(
            db, tenant_id=tenant_id, conversation_id=conversation_id, lock=True
        )
        if conversation.status != "ACTIVE":
            raise AnalysisChatError("ANALYSIS_CHAT_CONVERSATION_NOT_ACTIVE")
        parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
        parent_result = await db.get(AIResultRecord, conversation.parent_result_id)
        if (
            parent_run is None or parent_result is None
            or parent_run.tenant_id != tenant_id or parent_result.tenant_id != tenant_id
        ):
            raise AnalysisChatError("ANALYSIS_CHAT_PARENT_CONTRACT_INVALID")
        canonical_run, canonical_result = await AnalysisChatService._canonical_parent(
            db, tenant_id=tenant_id, run=parent_run, result=parent_result
        )
        if canonical_run.id != parent_run.id:
            if int(conversation.message_count or 0) != 0:
                raise AnalysisChatError("ANALYSIS_CHAT_NESTED_CONVERSATION_NOT_EMPTY")
            conversation.parent_analysis_run_id = canonical_run.id
            conversation.parent_result_id = canonical_result.id
            parent_run, parent_result = canonical_run, canonical_result
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
            turn_max_output_tokens = (
                config.proposal_max_output_tokens
                if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL
                else parent_approval.max_output_tokens
            )
            approval_payload = {
                "id": str(approval_id),
                "tenant_id": str(tenant_id),
                "provider": effective_provider,
                "model": effective_model,
                "max_cost_usd": str(config.provider_max_cost_usd),
                "max_output_tokens": int(turn_max_output_tokens),
                "budget_enforcement_enabled": config.budget_enforcement_enabled,
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
                max_output_tokens=turn_max_output_tokens,
                pricing_source_url=parent_approval.pricing_source_url,
                pricing_observed_at=parent_approval.pricing_observed_at,
                pricing_snapshot_hash=parent_approval.pricing_snapshot_hash,
                approval_phrase_hash=canonical_hash({
                    "action": "AUTHENTICATED_ANALYSIS_CHAT_SEND",
                    "user_message_id": str(user_message.id),
                    "max_cost_usd": str(config.provider_max_cost_usd),
                    "max_output_tokens": int(turn_max_output_tokens),
                    "budget_enforcement_enabled": config.budget_enforcement_enabled,
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
            budget.null_limit_policy = (
                "DENY" if config.budget_enforcement_enabled else "AUDIT_ONLY"
            )
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
                "budget_enforcement_enabled": config.budget_enforcement_enabled,
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
        db: AsyncSession,
        *,
        tenant_id: UUID,
        user_id: UUID,
        conversation_id: UUID,
        graph_run_id: UUID | None = None,
    ) -> AIAnalysisMessage | None:
        # Discover the run first, then acquire locks in the same run ->
        # conversation -> message order used by graph execution.  Re-check the
        # message under lock before changing any state.
        candidate_query = select(AIAnalysisMessage).where(
            AIAnalysisMessage.tenant_id == tenant_id,
            AIAnalysisMessage.conversation_id == conversation_id,
            AIAnalysisMessage.role == "ASSISTANT",
            AIAnalysisMessage.status.in_(("PENDING", "QUEUED", "STREAMING", "INTERRUPTED")),
        )
        if graph_run_id is not None:
            candidate_query = candidate_query.where(
                AIAnalysisMessage.graph_run_id == graph_run_id
            )
        candidate = (
            await db.execute(
                candidate_query.order_by(
                    AIAnalysisMessage.sequence_number.desc()
                ).limit(1)
            )
        ).scalar_one_or_none()
        if candidate is None:
            return None
        run = None
        if candidate.graph_run_id:
            run = (
                await db.execute(select(AIGraphRun).where(
                    AIGraphRun.id == candidate.graph_run_id,
                    AIGraphRun.tenant_id == tenant_id,
                ).with_for_update())
            ).scalar_one_or_none()
        conversation = await AnalysisChatService.get_conversation(
            db, tenant_id=tenant_id, conversation_id=conversation_id, lock=True
        )
        message = (
            await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.id == candidate.id,
                AIAnalysisMessage.tenant_id == tenant_id,
                AIAnalysisMessage.conversation_id == conversation.id,
                AIAnalysisMessage.role == "ASSISTANT",
                AIAnalysisMessage.status.in_(("PENDING", "QUEUED", "STREAMING", "INTERRUPTED")),
            ).with_for_update())
        ).scalar_one_or_none()
        if message is None:
            return None
        now = _now()
        reservation = None
        if message.ai_request_id:
            reservation = (await db.execute(select(AIBudgetReservationRecord).where(
                AIBudgetReservationRecord.tenant_id == tenant_id,
                AIBudgetReservationRecord.ai_request_id == message.ai_request_id,
            ).with_for_update())).scalar_one_or_none()
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
            usage = (await db.execute(select(AIUsageRecord).where(
                AIUsageRecord.tenant_id == tenant_id,
                AIUsageRecord.ai_request_id == message.ai_request_id,
            ))).scalar_one_or_none()
            if usage is not None:
                actual_input = int(usage.tokens_input)
                actual_output = int(usage.tokens_output)
                actual_cost = Decimal(usage.actual_cost)
                existing_usage = (
                    message.tokens_input,
                    message.tokens_output,
                    message.cost_usd,
                )
                if all(value is None for value in existing_usage):
                    message.tokens_input = actual_input
                    message.tokens_output = actual_output
                    message.cost_usd = actual_cost
                    conversation.total_tokens_input = (
                        int(conversation.total_tokens_input or 0) + actual_input
                    )
                    conversation.total_tokens_output = (
                        int(conversation.total_tokens_output or 0) + actual_output
                    )
                    conversation.total_cost_usd = (
                        Decimal(str(conversation.total_cost_usd or 0)) + actual_cost
                    )
                elif not (
                    all(value is not None for value in existing_usage)
                    and int(message.tokens_input) == actual_input
                    and int(message.tokens_output) == actual_output
                    and Decimal(message.cost_usd) == actual_cost
                ):
                    raise AnalysisChatError(
                        "ANALYSIS_CHAT_TERMINAL_USAGE_ATTRIBUTION_CONFLICT"
                    )
        transport_attempted = _cancel_budget_reservation(reservation, now=now)
        message.status = "CANCELLED"
        message.cancelled_at = now
        message.completed_at = now
        message.provider_transport_attempted = bool(
            message.provider_transport_attempted or transport_attempted
        )
        message.lock_version = int(message.lock_version or 0) + 1
        if run is not None and run.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            run.status = "CANCELLED"
            run.cancelled_at = now
            run.completed_at = now
            run.terminal_reason = "CANCELLED_BY_AUTHORIZED_ACTOR"
            run.provider_transport_attempted = message.provider_transport_attempted
            run.heartbeat_at = now
            run.lease_owner = None
            run.lease_expires_at = None
            run.updated_at = now
            db.add(AIGraphEvent(
                tenant_id=tenant_id,
                graph_run_id=run.id,
                event_key=f"{run.id}:cancelled",
                event_type="CANCELLED",
                node_name=run.current_node,
                status="CANCELLED",
                payload={
                    "actor_user_id": str(user_id),
                    "message_id": str(message.id),
                    "provider_transport_attempted": message.provider_transport_attempted,
                    "budget_reservation_status": (
                        reservation.status if reservation is not None else None
                    ),
                },
            ))
        conversation.updated_at = now
        conversation.lock_version = int(conversation.lock_version or 0) + 1
        return message
