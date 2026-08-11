"""Canonical provider and LangGraph bridge shared by every AI entrypoint."""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
import json
import os
from typing import Any, Awaitable, Callable
from uuid import UUID

from jsonschema import ValidationError, validate
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai_orchestration.budget_reservation_audit import BudgetReservationAudit
from ..ai_orchestration.contracts import AIRequestIntent, AIResult
from ..ai_orchestration.errors import ProviderBlockedError, ProviderTransportError
from ..ai_orchestration.provider_adapters import (
    AnthropicSDKTextAdapter, CopilotProviderTransport, anthropic_output_config,
    default_adapter_registry,
)
from ..ai_orchestration.runtime import ProviderResponse
from ..ai_orchestration.request_intent import resolve_request_intent, validate_provider_intent_gate
from ..ai_orchestration.sanitizer import TrustLabel, structured_block
from ..database import run_db_task
from ..models.ai_provider_key import AIProviderKey
from ..models.config_profile import ConfigProfile
from ..models.systemic_ai import (
    AIBudgetPolicyRecord, AIConfigurationBundleRecord, AIDatasetSnapshotRecord, AIModelResolutionRecord,
    AIModelApprovalRecord, AIPromptVersion, AIRequestRecord, AIResultRecord, AIUsageRecord,
    AIToolEvidenceRecord,
)
from .ai_keys_service import decrypt_value


class SystemicLangGraphBridge:
    """The only domain-facing transport bridge; graph nodes own provider execution."""

    @staticmethod
    async def create_legacy_run_if_enabled(
        db: AsyncSession,
        *,
        tenant_id: UUID,
        user_id: UUID,
        origin_module: str,
        origin_view: str,
        entity_ids: tuple[str, ...],
        filters: dict[str, Any],
        analysis_mode: str,
        question: str,
        authority: str,
        provider: str,
        model: str,
        correlation_identity: str,
        graph_key_override: str | None = None,
    ):
        from ..ai_orchestration.contracts import AnalysisMode, Authority
        from ..ai_orchestration.hashing import canonical_hash
        from ..ai_orchestration.langgraph.config import get_langgraph_settings
        from .module_ai_analysis_service import ModuleAIAnalysisService

        settings = get_langgraph_settings()
        if not (
            settings.runtime == "langgraph"
            and settings.runtime_enabled
            and settings.entrypoints_enabled
            and settings.module_flags.get(origin_module, False)
        ):
            return None
        now = datetime.now(timezone.utc)
        approval = (await db.execute(select(AIModelApprovalRecord).where(
            AIModelApprovalRecord.tenant_id == tenant_id,
            AIModelApprovalRecord.approved_by == user_id,
            AIModelApprovalRecord.provider == provider,
            AIModelApprovalRecord.model == model,
            AIModelApprovalRecord.scope == "SYSTEMIC_MODULE_ANALYSIS",
            AIModelApprovalRecord.status == "APPROVED",
            AIModelApprovalRecord.expires_at > now,
        ).order_by(AIModelApprovalRecord.approved_at.desc()).limit(1))).scalar_one_or_none()
        if approval is None:
            raise RuntimeError("MODEL_COST_APPROVAL_REQUIRED_FOR_LEGACY_BRIDGE")
        idempotency_key = "legacy-graph-" + canonical_hash({
            "origin_module": origin_module,
            "identity": correlation_identity,
            "provider": provider,
            "model": model,
        })
        return await ModuleAIAnalysisService.create_run(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            origin_module=origin_module,
            origin_view=origin_view,
            entity_ids=entity_ids,
            filters=filters,
            analysis_mode=AnalysisMode(analysis_mode),
            question=question,
            authority=Authority(authority),
            provider=provider,
            model=model,
            model_approval_id=approval.id,
            idempotency_key=idempotency_key,
            graph_key_override=graph_key_override,
        )

    @staticmethod
    async def execute_json_provider(
        *, provider: str, model: str, system_prompt: str, user_prompt: str,
        api_key: str, request_id: str, max_output_tokens: int,
        output_schema: dict[str, Any] | None = None,
    ) -> ProviderResponse:
        adapter = default_adapter_registry().get(provider)
        return await adapter.execute(
            provider=provider, model=model, system_prompt=system_prompt,
            user_prompt=user_prompt, tools=[], api_key=api_key, request_id=request_id,
            max_output_tokens=max_output_tokens, output_schema=output_schema,
        )

    @staticmethod
    async def execute_anthropic_text(
        *, api_key: str, model: str, prompt: str, max_tokens: int,
        system_prompt: str | None = None,
    ):
        return await AnthropicSDKTextAdapter().execute(
            api_key=api_key, model=model, prompt=prompt, max_tokens=max_tokens,
            system_prompt=system_prompt,
        )

    @staticmethod
    async def execute_copilot(
        *, provider: str, api_key: str, model: str, system: str, message: str,
        tools: list[dict[str, Any]], tool_callback: Callable[[str, dict[str, Any]], Awaitable[Any]],
        max_rounds: int, final_instruction: str,
    ) -> str:
        transport = CopilotProviderTransport()
        kwargs = dict(
            api_key=api_key, model=model, system=system, message=message, tools=tools,
            tool_callback=tool_callback, max_rounds=max_rounds,
            final_instruction=final_instruction,
        )
        if provider == "openai":
            return await transport.openai(**kwargs)
        if provider == "anthropic":
            return await transport.anthropic(**kwargs)
        raise ValueError("Co-Pilot provider is not supported")

    @staticmethod
    async def execute_prepared_request(db: AsyncSession, request: AIRequestRecord) -> dict[str, Any]:
        from ..ai_orchestration.langgraph.config import get_langgraph_settings

        settings = get_langgraph_settings()
        request_json = dict(request.request_json or {})
        request_intent = resolve_request_intent(request_json)
        settings.require_runtime()
        environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower()
        fake_enabled = os.getenv(
            "LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "false",
        ).lower() == "true"
        if request_intent is AIRequestIntent.FAKE_PROVIDER_CANARY:
            validate_provider_intent_gate(
                request_intent,
                environment_name=environment,
                fake_provider_canary_enabled=fake_enabled,
                real_provider_canary_enabled=settings.real_provider_canary_enabled,
                normal_analysis_provider_enabled=False,
            )
            resolution = await db.get(AIModelResolutionRecord, request.model_resolution_id)
            existing = (await db.execute(select(AIResultRecord).where(
                AIResultRecord.tenant_id == request.tenant_id,
                AIResultRecord.ai_request_id == request.id,
            ))).scalar_one_or_none()
            if existing is not None:
                return dict(existing.result_json)
            fake_result = request_json.get("fake_provider_result")
            if (
                resolution is None
                or resolution.effective_provider != "fake"
                or resolution.effective_model != "fake-analysis-v1"
                or not isinstance(fake_result, dict)
            ):
                raise ProviderBlockedError(
                    "FAKE_PROVIDER_CANARY_INVALID",
                    "Fake provider canary fixture or model resolution is invalid",
                )
            try:
                validated_fake_result = AIResult.model_validate(fake_result)
            except Exception as exc:
                raise ProviderBlockedError(
                    "FAKE_PROVIDER_CANARY_RESULT_INVALID",
                    "Fake provider canary result failed typed validation",
                ) from exc
            tool_evidence_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
                AIToolEvidenceRecord.tenant_id == request.tenant_id,
                AIToolEvidenceRecord.ai_request_id == request.id,
            ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id))).scalars().all())
            if not tool_evidence_rows:
                raise RuntimeError("GRAPH_TYPED_TOOL_EVIDENCE_REQUIRED")
            await run_db_task(
                lambda reservation_db: BudgetReservationAudit.record_zero_cost_fake(
                    reservation_db,
                    tenant_id=request.tenant_id,
                    ai_request_id=request.id,
                    budget_policy_id=None,
                    model_approval_id=None,
                    request_intent=request_intent.value,
                    provider="fake",
                    model="fake-analysis-v1",
                    module=request.origin_module,
                ),
                celery=True,
            )
            result_json = validated_fake_result.model_dump(mode="json")
            result_json["tool_calls"] = [{
                "tool_call_audit_id": str(row.tool_call_audit_id),
                "evidence_id": str(row.id),
                "module": row.module_key,
                "tool": row.tool_name,
                "output_hash": row.output_hash,
                "quality": row.quality,
            } for row in tool_evidence_rows]
            db.add(AIResultRecord(
                tenant_id=request.tenant_id,
                ai_request_id=request.id,
                status="COMPLETED",
                result_json=result_json,
                terminal_reason="STAGING_CANARY",
                completed_at=datetime.now(timezone.utc),
            ))
            usage = (await db.execute(select(AIUsageRecord).where(
                AIUsageRecord.tenant_id == request.tenant_id,
                AIUsageRecord.ai_request_id == request.id,
            ))).scalar_one_or_none()
            if usage is None:
                db.add(AIUsageRecord(
                    tenant_id=request.tenant_id,
                    ai_request_id=request.id,
                    provider="fake",
                    model="fake-analysis-v1",
                    module=request.origin_module,
                    tokens_input=0,
                    tokens_output=0,
                    estimated_cost=Decimal("0"),
                    actual_cost=Decimal("0"),
                    currency="USD",
                    pricing_snapshot_version="ZERO_COST_FAKE_STAGING",
                ))
            await db.flush()
            return result_json
        existing = (await db.execute(select(AIResultRecord).where(
            AIResultRecord.tenant_id == request.tenant_id,
            AIResultRecord.ai_request_id == request.id,
        ))).scalar_one_or_none()
        if existing is not None:
            return dict(existing.result_json)
        existing_usage = (await db.execute(select(AIUsageRecord).where(
            AIUsageRecord.tenant_id == request.tenant_id,
            AIUsageRecord.ai_request_id == request.id,
        ).order_by(AIUsageRecord.created_at.desc()).limit(1))).scalar_one_or_none()
        if existing_usage is not None:
            return {
                "ai_request_id": str(request.id),
                "status": "FAILED",
                "terminal_reason": "PROVIDER_CALL_ALREADY_RECONCILED",
                "error_code": "PROVIDER_CALL_ALREADY_RECONCILED_NO_RETRY",
                "provider_output_schema_valid": False,
            }
        resolution = await db.get(AIModelResolutionRecord, request.model_resolution_id)
        prompt = await db.get(AIPromptVersion, request.prompt_version_id)
        dataset = await db.get(AIDatasetSnapshotRecord, request.dataset_snapshot_id)
        bundle = await db.get(AIConfigurationBundleRecord, request.configuration_bundle_id)
        if not all((resolution, prompt, dataset, bundle)):
            raise RuntimeError("GRAPH_CANONICAL_LINEAGE_INVALID")
        if any(item.tenant_id != request.tenant_id for item in (resolution, dataset, bundle)):
            raise RuntimeError("GRAPH_CANONICAL_TENANT_MISMATCH")
        if resolution.configured_model != resolution.effective_model:
            raise RuntimeError("CONFIGURED_EFFECTIVE_MODEL_MISMATCH")

        tool_evidence_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
            AIToolEvidenceRecord.tenant_id == request.tenant_id,
            AIToolEvidenceRecord.ai_request_id == request.id,
        ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id))).scalars().all())
        if not tool_evidence_rows:
            raise RuntimeError("GRAPH_TYPED_TOOL_EVIDENCE_REQUIRED")

        normal_provider_enabled = False
        if request_intent is AIRequestIntent.NORMAL_ANALYSIS:
            runtime_config = (await db.execute(select(ConfigProfile).where(
                ConfigProfile.user_id == request.tenant_id,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == "ai_provider_runtime",
                ConfigProfile.is_active.is_(True),
            ).order_by(ConfigProfile.updated_at.desc()).limit(1))).scalar_one_or_none()
            normal_provider_enabled = bool(
                runtime_config
                and (runtime_config.config_json or {}).get("normal_analysis_provider_enabled") is True
            )
        validate_provider_intent_gate(
            request_intent,
            environment_name=environment,
            fake_provider_canary_enabled=fake_enabled,
            real_provider_canary_enabled=settings.real_provider_canary_enabled,
            normal_analysis_provider_enabled=normal_provider_enabled,
        )

        key = (await db.execute(select(AIProviderKey).where(
            AIProviderKey.user_id == request.tenant_id,
            AIProviderKey.provider == resolution.effective_provider,
            AIProviderKey.is_active.is_(True),
            AIProviderKey.is_validated.is_(True),
        ).with_for_update())).scalar_one_or_none()
        if key is None or key.monthly_token_limit is None:
            raise RuntimeError("VALIDATED_PROVIDER_KEY_AND_BUDGET_REQUIRED")
        if int(key.tokens_used_month or 0) >= int(key.monthly_token_limit):
            raise RuntimeError("MONTHLY_AI_BUDGET_EXHAUSTED")

        frozen_context = request_json.get("frozen_context") or {}
        try:
            approval_id = UUID(str(frozen_context["model_approval_id"]))
        except (KeyError, ValueError) as exc:
            raise RuntimeError("MODEL_COST_APPROVAL_REQUIRED") from exc
        approval = await db.get(AIModelApprovalRecord, approval_id)
        if (
            approval is None
            or approval.tenant_id != request.tenant_id
            or approval.provider != resolution.effective_provider
            or approval.model != resolution.effective_model
            or approval.scope != "SYSTEMIC_MODULE_ANALYSIS"
            or approval.status != "APPROVED"
            or approval.expires_at <= datetime.now(timezone.utc)
        ):
            raise RuntimeError("MODEL_COST_APPROVAL_INVALID")
        budget = (await db.execute(select(AIBudgetPolicyRecord).where(
            AIBudgetPolicyRecord.tenant_id == request.tenant_id,
            AIBudgetPolicyRecord.provider == resolution.effective_provider,
            AIBudgetPolicyRecord.model == resolution.effective_model,
            AIBudgetPolicyRecord.module == request.origin_module,
            AIBudgetPolicyRecord.is_active.is_(True),
        ))).scalar_one_or_none()
        if (
            budget is None
            or budget.null_limit_policy != "DENY"
            or budget.daily_token_limit is None
            or budget.monthly_token_limit is None
        ):
            raise RuntimeError("BOUNDED_AI_BUDGET_POLICY_REQUIRED")
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        used_today = int((await db.scalar(select(func.coalesce(
            func.sum(AIUsageRecord.tokens_input + AIUsageRecord.tokens_output), 0,
        )).where(
            AIUsageRecord.tenant_id == request.tenant_id,
            AIUsageRecord.provider == resolution.effective_provider,
            AIUsageRecord.model == resolution.effective_model,
            AIUsageRecord.module == request.origin_module,
            AIUsageRecord.created_at >= day_start,
        ))) or 0)
        used_month = int((await db.scalar(select(func.coalesce(
            func.sum(AIUsageRecord.tokens_input + AIUsageRecord.tokens_output), 0,
        )).where(
            AIUsageRecord.tenant_id == request.tenant_id,
            AIUsageRecord.provider == resolution.effective_provider,
            AIUsageRecord.model == resolution.effective_model,
            AIUsageRecord.module == request.origin_module,
            AIUsageRecord.created_at >= month_start,
        ))) or 0)
        question = structured_block(TrustLabel.USER_INPUT, str(request_json.get("question") or ""))
        tool_evidence = [{
            "evidence_id": str(row.id),
            "module": row.module_key,
            "tool": row.tool_name,
            "output_hash": row.output_hash,
            "quality": row.quality,
            "freshness": row.freshness_json,
            "output": row.output_json,
        } for row in tool_evidence_rows]
        context_json = json.dumps({
            "frozen_context": frozen_context,
            "typed_tool_evidence": tool_evidence,
        }, ensure_ascii=False, default=str)
        values = {
            "question": question,
            "evidence": context_json,
            "dataset": context_json,
            "configuration": json.dumps(bundle.bundle_json, ensure_ascii=False, default=str),
            "context": context_json,
        }
        try:
            system_prompt = prompt.system_template.format_map(values)
            user_prompt = prompt.user_template.format_map(values)
        except KeyError as exc:
            raise RuntimeError(f"PROMPT_INPUT_MISSING:{exc.args[0]}") from exc

        # UTF-8 byte length is a deliberately conservative reservation bound;
        # it avoids under-reserving for non-ASCII prompt content.
        structured_output_reservation = 0
        if resolution.effective_provider == "anthropic":
            output_config = anthropic_output_config(prompt.output_schema_json)
            # Reserve the complete transport contract plus bounded room for the
            # provider-injected structured-output instruction.
            structured_output_reservation = len(json.dumps(
                {"output_config": output_config},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")) + 512
        estimated_input_tokens = max(
            1,
            len((system_prompt + user_prompt).encode("utf-8"))
            + structured_output_reservation,
        )
        max_input_tokens = int(budget.request_token_limit) - int(approval.max_output_tokens)
        if max_input_tokens <= 0 or estimated_input_tokens > max_input_tokens:
            raise RuntimeError("AI_INPUT_RESERVATION_EXCEEDED")
        reserved_tokens = estimated_input_tokens + int(approval.max_output_tokens)
        if reserved_tokens > int(budget.request_token_limit):
            raise RuntimeError("AI_REQUEST_TOKEN_BUDGET_EXCEEDED")
        if used_today + reserved_tokens > int(budget.daily_token_limit):
            raise RuntimeError("AI_DAILY_TOKEN_BUDGET_EXCEEDED")
        if used_month + reserved_tokens > int(budget.monthly_token_limit):
            raise RuntimeError("AI_MONTHLY_TOKEN_BUDGET_EXCEEDED")
        million = Decimal("1000000")
        input_rate = Decimal(approval.input_cost_per_million)
        output_rate = Decimal(approval.output_cost_per_million)
        reserved_cost = (
            Decimal(estimated_input_tokens) * input_rate
            + Decimal(approval.max_output_tokens) * output_rate
        ) / million
        if reserved_cost > Decimal(approval.max_cost_usd):
            raise RuntimeError("MODEL_COST_APPROVAL_LIMIT_EXCEEDED_BEFORE_CALL")

        api_key = decrypt_value(bytes(key.api_key_encrypted))
        reservation = await run_db_task(
            lambda reservation_db: BudgetReservationAudit.reserve(
                reservation_db,
                tenant_id=request.tenant_id,
                ai_request_id=request.id,
                budget_policy_id=budget.id,
                model_approval_id=approval.id,
                request_intent=request_intent.value,
                provider=resolution.effective_provider,
                model=resolution.effective_model,
                module=request.origin_module,
                estimated_input_tokens=estimated_input_tokens,
                max_output_tokens=int(approval.max_output_tokens),
                request_token_limit=int(budget.request_token_limit),
                daily_token_limit=int(budget.daily_token_limit),
                monthly_token_limit=int(budget.monthly_token_limit),
                reserved_tokens=reserved_tokens,
                reserved_cost_usd=reserved_cost,
            ),
            celery=True,
        )
        if not reservation["created"]:
            raise ProviderBlockedError(
                "BUDGET_RESERVATION_ALREADY_EXISTS_NO_RETRY",
                "A budget reservation already exists; provider transport was not retried",
            )
        try:
            await run_db_task(
                lambda reservation_db: BudgetReservationAudit.mark_transport_started(
                    reservation_db,
                    tenant_id=request.tenant_id,
                    ai_request_id=request.id,
                ),
                celery=True,
            )
        except Exception:
            await run_db_task(
                lambda reservation_db: BudgetReservationAudit.release_before_transport(
                    reservation_db,
                    tenant_id=request.tenant_id,
                    ai_request_id=request.id,
                    reason_code="TRANSPORT_START_AUDIT_FAILED",
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
                api_key=api_key,
                request_id=str(request.id),
                max_output_tokens=int(approval.max_output_tokens),
                output_schema=prompt.output_schema_json,
            )
        except Exception as exc:
            await run_db_task(
                lambda reservation_db: BudgetReservationAudit.mark_transport_error(
                    reservation_db,
                    tenant_id=request.tenant_id,
                    ai_request_id=request.id,
                    reason_code="PROVIDER_TRANSPORT_FAILED",
                ),
                celery=True,
            )
            raise ProviderTransportError() from exc
        token_total = int(response.tokens_input) + int(response.tokens_output)
        actual_cost = (
            Decimal(response.tokens_input) * input_rate
            + Decimal(response.tokens_output) * output_rate
        ) / million
        reconciliation_error = next((code for exceeded, code in (
            (token_total > int(budget.request_token_limit), "AI_REQUEST_TOKEN_RECONCILIATION_EXCEEDED"),
            (used_today + token_total > int(budget.daily_token_limit), "AI_DAILY_TOKEN_RECONCILIATION_EXCEEDED"),
            (used_month + token_total > int(budget.monthly_token_limit), "AI_MONTHLY_TOKEN_RECONCILIATION_EXCEEDED"),
            (int(key.tokens_used_month or 0) + token_total > int(key.monthly_token_limit), "MONTHLY_AI_BUDGET_RECONCILIATION_EXCEEDED"),
            (actual_cost > Decimal(approval.max_cost_usd), "MODEL_COST_APPROVAL_RECONCILIATION_EXCEEDED"),
        ) if exceeded), None)
        reservation_reconciliation = await run_db_task(
            lambda reservation_db: BudgetReservationAudit.reconcile(
                reservation_db,
                tenant_id=request.tenant_id,
                ai_request_id=request.id,
                reserved_tokens=reserved_tokens,
                actual_tokens=token_total,
                actual_cost_usd=actual_cost,
                terminal_reason=str(response.terminal_error_code or "PROVIDER_RESPONSE_RECEIVED"),
            ),
            celery=True,
        )

        # A provider call is an external side effect. Persist its usage before
        # validating content so a rejected response remains fully auditable and
        # the graph can never repeat the paid request on resume/retry.
        key.tokens_used_month = int(key.tokens_used_month or 0) + token_total
        key.last_used_at = datetime.now(timezone.utc)
        db.add(AIUsageRecord(
            tenant_id=request.tenant_id, ai_request_id=request.id,
            provider=resolution.effective_provider, model=resolution.effective_model,
            module=request.origin_module, tokens_input=int(response.tokens_input),
            tokens_output=int(response.tokens_output), estimated_cost=reserved_cost,
            actual_cost=actual_cost, currency="USD",
            pricing_snapshot_version=approval.pricing_snapshot_hash,
        ))

        if response.terminal_error_code is not None:
            return {
                "ai_request_id": str(request.id),
                "status": "FAILED",
                "terminal_reason": response.terminal_error_code,
                "error_code": response.terminal_error_code,
                "provider_output_schema_valid": False,
                "provider_stop_reason": response.stop_reason,
                "provider_response_ref": response.raw_response_ref,
            }

        try:
            validate(response.output, prompt.output_schema_json)
        except ValidationError as exc:
            return {
                "ai_request_id": str(request.id),
                "status": "FAILED",
                "terminal_reason": "OUTPUT_SCHEMA_INVALID",
                "error_code": "OUTPUT_SCHEMA_INVALID",
                "provider_output_schema_valid": False,
                "provider_stop_reason": response.stop_reason,
                "provider_response_ref": response.raw_response_ref,
                "schema_error_path": [str(item) for item in exc.absolute_path][:8],
                "schema_validator": str(exc.validator),
            }
        if reconciliation_error is not None:
            return {
                "ai_request_id": str(request.id),
                "status": "FAILED",
                "terminal_reason": "PROVIDER_USAGE_RECONCILIATION_DENIED",
                "error_code": reconciliation_error,
                "provider_output_schema_valid": True,
            }

        result_json = {
            "ai_request_id": str(request.id),
            "status": "COMPLETED",
            "tenant_id": str(request.tenant_id),
            "provider": resolution.effective_provider,
            "requested_model": resolution.requested_model,
            "configured_model": resolution.configured_model,
            "effective_model": resolution.effective_model,
            "model_resolution_id": str(resolution.id),
            "prompt_version_id": str(prompt.id),
            "prompt_hash": prompt.content_hash,
            "dataset_snapshot_id": str(dataset.id),
            "dataset_hash": dataset.dataset_hash,
            "configuration_bundle_id": str(bundle.id),
            "configuration_bundle_hash": bundle.bundle_hash,
            "analysis": {
                key: value for key, value in response.output.items()
                if key not in {"recommendations", "warnings", "limitations"}
            },
            "recommendations": response.output.get("recommendations") or [],
            "warnings": response.output.get("warnings") or [],
            "limitations": response.output.get("limitations") or [],
            "context_manifest": dataset.context_manifest,
            "usage": {
                "tokens_input": int(response.tokens_input),
                "tokens_output": int(response.tokens_output),
                "estimated_cost": str(reserved_cost),
                "actual_cost": str(actual_cost),
                "currency": "USD",
                "pricing_snapshot_version": approval.pricing_snapshot_hash,
                "model_approval_id": str(approval.id),
                "max_cost_usd": str(approval.max_cost_usd),
                "input_cost_per_million": str(input_rate),
                "output_cost_per_million": str(output_rate),
                "pricing_source_url": approval.pricing_source_url,
                "cost_formula": "(tokens_input*input_rate + tokens_output*output_rate)/1000000",
                "reserved_tokens": reserved_tokens,
                "request_token_limit": int(budget.request_token_limit),
                "daily_token_limit": int(budget.daily_token_limit),
                "monthly_token_limit": int(budget.monthly_token_limit),
                "used_today_before": used_today,
                "used_month_before": used_month,
                "reservation_reconciled": token_total <= reserved_tokens,
                "budget_reservation_id": str(reservation["id"]),
                "released_tokens": reservation_reconciliation["released_tokens"],
                "overage_tokens": reservation_reconciliation["overage_tokens"],
            },
        }
        db.add(AIResultRecord(
            tenant_id=request.tenant_id, ai_request_id=request.id, status="COMPLETED",
            result_json=result_json, terminal_reason="ANALYSIS_ONLY_COMPLETED",
        ))
        await db.flush()
        return result_json
