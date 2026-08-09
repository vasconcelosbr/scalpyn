from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from jsonschema import ValidationError, validate

from .budget_service import BudgetPolicy, BudgetService
from .configuration_bundle_service import ConfigurationBundle
from .context import TenantAIContext
from .contracts import AIRequest, AIResult, AIUsage
from .dataset_service import CanonicalAnalysisDataset, CanonicalDatasetService
from .errors import AIErrorCode, fail
from .invariant_validator import InvariantValidator, RuntimeInvariantState
from .job_service import AIJobState, LeaseJob
from .prompt_registry import PromptRegistry
from .provider_registry import ProviderModelRegistry
from .runtime import ProviderAdapterRegistry
from .sanitizer import TrustLabel, structured_block
from .tool_registry import ToolRegistry


PersistHook = Callable[[str, Any], Awaitable[None]]


class AIOrchestrationService:
    """Fail-closed orchestration pipeline shared by every provider entrypoint."""

    def __init__(self, *, model_registry: ProviderModelRegistry, prompt_registry: PromptRegistry,
                 adapters: ProviderAdapterRegistry, tool_registry: ToolRegistry,
                 key_resolver: Callable[[TenantAIContext, str], Awaitable[str]],
                 persist: PersistHook | None = None):
        self.model_registry = model_registry
        self.prompt_registry = prompt_registry
        self.adapters = adapters
        self.tool_registry = tool_registry
        self.key_resolver = key_resolver
        self.persist = persist
        self.budget = BudgetService()
        self.dataset = CanonicalDatasetService()
        self.invariants = InvariantValidator()

    async def execute(self, request: AIRequest, *, context: TenantAIContext,
                      configured_provider: str | None, configured_model: str | None,
                      dataset: CanonicalAnalysisDataset, bundle: ConfigurationBundle,
                      budget_policy: BudgetPolicy, used_today: int, used_month: int,
                      prompt_values: dict[str, Any], runtime_state: RuntimeInvariantState,
                      estimated_input_tokens: int = 4_000, estimated_output_tokens: int = 1_000) -> AIResult:
        if request.tenant_id != context.tenant_id:
            raise fail(AIErrorCode.TENANT_SCOPE_MISSING, "Authenticated tenant does not match the AI request", http_status=403)
        if dataset.tenant_id != context.tenant_id or bundle.tenant_id != context.tenant_id:
            raise fail(AIErrorCode.TENANT_SCOPE_MISSING, "Dataset or configuration bundle is cross-tenant", http_status=403)

        resolution = self.model_registry.resolve(
            requested_provider=request.provider_request.provider,
            requested_model=request.provider_request.model,
            configured_provider=configured_provider,
            configured_model=configured_model,
            allow_request_override=request.provider_request.allow_request_override,
            required_capabilities=request.provider_request.required_capabilities,
        )
        prompt = self.prompt_registry.resolve(request.prompt_key, request.prompt_version)
        self.dataset.enforce_quality(dataset, request.authority)
        self.invariants.validate(request, bundle=bundle, dataset=dataset, state=runtime_state)
        reservation = self.budget.admit(
            budget_policy, estimated_input=estimated_input_tokens, estimated_output=estimated_output_tokens,
            used_today=used_today, used_month=used_month,
        )

        sanitized_values = dict(prompt_values)
        sanitized_values["question"] = structured_block(TrustLabel.USER_INPUT, request.question)
        system_prompt, user_prompt = self.prompt_registry.render(prompt, sanitized_values)
        job = LeaseJob.queued(tenant_id=context.tenant_id, purpose=request.origin_module, identity={
            "dataset": dataset.dataset_hash, "bundle": bundle.bundle_hash, "prompt": prompt.content_hash,
            "provider": resolution.effective_provider, "model": resolution.effective_model,
        }).acquire(f"orchestrator:{request.ai_request_id}")

        if self.persist:
            for kind, value in (("model_resolution", resolution), ("prompt", prompt),
                                ("configuration_bundle", bundle), ("dataset", dataset),
                                ("request", request), ("budget_reservation", reservation), ("job", job)):
                await self.persist(kind, value)

        api_key = await self.key_resolver(context, resolution.effective_provider)
        if not api_key:
            raise fail(AIErrorCode.PROVIDER_NOT_CONFIGURED, "No validated tenant-scoped provider key is available", http_status=409)
        adapter = self.adapters.get(resolution.effective_provider)
        response = await adapter.execute(
            provider=resolution.effective_provider, model=resolution.effective_model,
            system_prompt=system_prompt, user_prompt=user_prompt, tools=[], api_key=api_key,
            request_id=str(request.ai_request_id), max_output_tokens=estimated_output_tokens,
        )
        try:
            validate(response.output, prompt.output_schema_json)
        except ValidationError as exc:
            raise fail(AIErrorCode.OUTPUT_SCHEMA_INVALID, "Provider output did not match the approved schema") from exc
        usage_data = self.budget.reconcile(
            reservation, actual_input=response.tokens_input, actual_output=response.tokens_output,
            actual_cost=Decimal("0"),
        )
        usage = AIUsage(
            tokens_input=response.tokens_input, tokens_output=response.tokens_output,
            pricing_snapshot_version="UNPRICED_STAGING_V1", reservation=Decimal(reservation.estimated_tokens),
            limit=Decimal(budget_policy.monthly_token_limit) if budget_policy.monthly_token_limit is not None else None,
            remaining=Decimal(reservation.remaining_tokens),
        )
        completed = datetime.now(timezone.utc)
        result = AIResult(
            ai_request_id=request.ai_request_id, status="COMPLETED", tenant_id=context.tenant_id,
            provider=resolution.effective_provider, requested_model=request.provider_request.model,
            configured_model=configured_model, effective_model=resolution.effective_model,
            model_resolution_id=resolution.id, prompt_version_id=prompt.id, prompt_hash=prompt.content_hash,
            dataset_snapshot_id=dataset.dataset_snapshot_id, dataset_hash=dataset.dataset_hash,
            configuration_bundle_id=bundle.configuration_bundle_id, configuration_bundle_hash=bundle.bundle_hash,
            analysis=response.output.get("analysis") or {},
            recommendations=tuple(response.output.get("recommendations") or []),
            usage=usage, completed_at=completed,
        )
        if self.persist:
            await self.persist("usage", usage_data)
            await self.persist("result", result)
            await self.persist("job", job.terminalize(status=AIJobState.COMPLETED, now=completed))
        return result
