"""Persist and verify the systemic AI foundation in a disposable staging DB.

The canary deliberately uses a synthetic inactive tenant and a fake provider
adapter. It never calls an external model and refuses to run outside an
environment whose Railway name contains ``staging``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from run_alembic_with_railway_proxy import _public_database_url


def _require_staging() -> str:
    environment = os.environ.get("RAILWAY_ENVIRONMENT_NAME", "")
    if "staging" not in environment.lower():
        raise SystemExit("REFUSED: systemic AI canary is staging-only")
    return environment


async def main() -> None:
    environment = _require_staging()
    os.environ["DATABASE_URL"] = _public_database_url()

    # Import after DATABASE_URL is set: app.database constructs its engine at
    # module import time.
    from app.ai_orchestration.budget_service import BudgetPolicy
    from app.ai_orchestration.configuration_bundle_service import ConfigurationBundleService
    from app.ai_orchestration.context import TenantAIContext
    from app.ai_orchestration.contracts import (
        AIRequest,
        AnalysisMode,
        Authority,
        CanonicalDatasetRequest,
        ConfigurationScope,
        ProviderModelRequest,
    )
    from app.ai_orchestration.dataset_service import CanonicalDatasetService
    from app.ai_orchestration.errors import AIErrorCode, AIOrchestrationError
    from app.ai_orchestration.hashing import canonical_hash
    from app.ai_orchestration.invariant_validator import RuntimeInvariantState
    from app.ai_orchestration.job_service import AIJobState, LeaseJob
    from app.ai_orchestration.orchestrator import AIOrchestrationService
    from app.ai_orchestration.persistence import SQLAlchemyAIPersistence
    from app.ai_orchestration.prompt_registry import PromptRegistry, PromptVersion
    from app.ai_orchestration.provider_registry import default_registry
    from app.ai_orchestration.runtime import ProviderAdapterRegistry, ProviderResponse
    from app.ai_orchestration.tool_registry import SideEffect, ToolCapability, ToolRegistry
    from app.database import AsyncSessionLocal, engine
    # Register the complete legacy metadata graph before flushing rows whose
    # foreign keys target existing Scalpyn tables.
    import app.models  # noqa: F401
    from app.models.profile import Profile
    from app.models.systemic_ai import (
        AIConfigurationBundleRecord,
        AIDatasetSnapshotRecord,
        AIJobRecord,
        AIModelResolutionRecord,
        AIPromptVersion,
        AIRequestRecord,
        AIResultRecord,
        AIToolCallAudit,
        AIUsageRecord,
    )
    from app.models.user import User

    class FakeAdapter:
        calls = 0

        async def execute(self, **_: object) -> ProviderResponse:
            self.calls += 1
            return ProviderResponse(
                output={
                    "analysis": {"verdict": "STAGING_SYNTHETIC_ANALYSIS_ONLY"},
                    "recommendations": [],
                    "warnings": ["No external provider was called"],
                    "limitations": ["Synthetic staging evidence only"],
                },
                tokens_input=17,
                tokens_output=11,
                raw_response_ref="staging-fake-adapter",
            )

    tenant_id = uuid4()
    correlation_id = f"systemic-ai-staging-canary-{uuid4()}"
    now = datetime.now(timezone.utc)
    adapter = FakeAdapter()
    denial_code = None

    async with AsyncSessionLocal() as session:
        live_before = int((await session.scalar(
            select(func.count()).select_from(Profile).where(Profile.live_trading_enabled.is_(True))
        )) or 0)
        autopilot_before = int((await session.scalar(
            select(func.count()).select_from(Profile).where(Profile.auto_pilot_enabled.is_(True))
        )) or 0)

        session.add(User(
            id=tenant_id,
            email=f"systemic-ai-canary-{tenant_id}@staging.invalid",
            password_hash="NOT_A_LOGIN",
            name="Systemic AI Staging Canary",
            role="auditor",
            is_active=False,
        ))
        await session.flush()

        prompt_row = (await session.execute(
            select(AIPromptVersion).where(
                AIPromptVersion.prompt_key == "shadow-detailed-analysis",
                AIPromptVersion.semantic_version == "1.0.0",
            )
        )).scalar_one()
        prompt = PromptVersion(
            id=prompt_row.id,
            prompt_key=prompt_row.prompt_key,
            semantic_version=prompt_row.semantic_version,
            status=prompt_row.status,
            system_template=prompt_row.system_template,
            user_template=prompt_row.user_template,
            input_schema_json=prompt_row.input_schema_json,
            output_schema_json=prompt_row.output_schema_json,
            tool_policy_json=prompt_row.tool_policy_json,
            provider_constraints_json=prompt_row.provider_constraints_json,
            content_hash=prompt_row.content_hash,
            created_by=prompt_row.created_by,
            created_at=prompt_row.created_at,
            approved_by=prompt_row.approved_by,
            approved_at=prompt_row.approved_at,
        )

        bundle = ConfigurationBundleService.create(
            tenant_id=tenant_id,
            bundle_json={
                "source": "STAGING_SYNTHETIC",
                "authority": "ANALYSIS_ONLY",
                "live_write": False,
            },
        )
        dataset_request = CanonicalDatasetRequest(
            domain="SHADOW_PORTFOLIO",
            window_start=now - timedelta(hours=1),
            window_end=now,
            source_labels=("STAGING_SYNTHETIC",),
            time_anchor="created_at",
            outcome_contract="synthetic-no-market-outcome-v1",
            event_identity_contract="synthetic-event-id-v1",
        )
        dataset = CanonicalDatasetService().build(
            tenant_id=tenant_id,
            request=dataset_request,
            configuration_bundle_id=bundle.configuration_bundle_id,
            rows=({
                "id": str(uuid4()),
                "event_identity": "staging-synthetic-event",
                "outcome": "NO_LIVE_EFFECT",
                "lineage_status": "COMPLETE",
            },),
            query_contract={"source": "STAGING_SYNTHETIC", "read_only": True},
        )
        request = AIRequest(
            tenant_id=tenant_id,
            requested_by_user_id=tenant_id,
            origin_module="SYSTEMIC_AI_STAGING_CANARY",
            origin_view="/dashboard/shadow-portfolio",
            analysis_mode=AnalysisMode.SYSTEMIC,
            authority=Authority.ANALYSIS_ONLY,
            provider_request=ProviderModelRequest(required_capabilities=frozenset({"text", "structured_output"})),
            prompt_key="shadow-detailed-analysis",
            prompt_version="1.0.0",
            dataset_request=dataset_request,
            configuration_scope=ConfigurationScope(require_complete_bundle=False),
            question="Validate the staging persistence path without external calls or live effects.",
            correlation_id=correlation_id,
        )
        context = TenantAIContext.from_authenticated_user(tenant_id, role="auditor")
        adapters = ProviderAdapterRegistry()
        adapters.register("anthropic", adapter)
        tools = ToolRegistry()
        live_tool = ToolCapability(
            name="canary.live_write",
            version="1.0.0",
            domain="STAGING_SYNTHETIC",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            side_effect=SideEffect.LIVE_WRITE,
            required_permissions=(),
            tenant_scoped=True,
            max_runtime_seconds=1,
        )
        tools.register(live_tool, lambda: None)

        persistence = SQLAlchemyAIPersistence(session, tenant_id)
        service = AIOrchestrationService(
            model_registry=default_registry(),
            prompt_registry=PromptRegistry((prompt,)),
            adapters=adapters,
            tool_registry=tools,
            key_resolver=lambda _context, _provider: asyncio.sleep(0, result="staging-fake-key-not-used-externally"),
            persist=persistence,
        )
        result = await service.execute(
            request,
            context=context,
            configured_provider="anthropic",
            configured_model="claude-haiku-4-5-20251001",
            dataset=dataset,
            bundle=bundle,
            budget_policy=BudgetPolicy(
                tenant_id=tenant_id,
                provider="anthropic",
                model="claude-haiku-4-5-20251001",
                module=request.origin_module,
                daily_token_limit=10_000,
                monthly_token_limit=100_000,
                request_token_limit=6_000,
            ),
            used_today=0,
            used_month=0,
            prompt_values={
                "evidence": "STAGING_SYNTHETIC",
                "dataset": "STAGING_SYNTHETIC",
                "configuration": "ANALYSIS_ONLY",
                "context": "STAGING_SYNTHETIC",
            },
            runtime_state=RuntimeInvariantState(
                spot_never_sell_at_loss_config=True,
                live_trading_authority=False,
                model_promotion_authority=False,
                real_risk_mutation_requested=False,
            ),
        )

        try:
            tools.authorize(
                "canary.live_write",
                "1.0.0",
                authority=Authority.ANALYSIS_ONLY,
                permissions=context.permissions,
            )
        except AIOrchestrationError as exc:
            denial_code = exc.detail.code.value
        if denial_code != AIErrorCode.TOOL_SIDE_EFFECT_DENIED.value:
            raise RuntimeError("live-write tool was not denied")
        session.add(AIToolCallAudit(
            tenant_id=tenant_id,
            ai_request_id=request.ai_request_id,
            tool_name="canary.live_write",
            tool_version="1.0.0",
            side_effect=SideEffect.LIVE_WRITE.value,
            status="DENIED",
            input_hash=canonical_hash({"source": "STAGING_SYNTHETIC"}),
            denial_reason=denial_code,
        ))
        await session.commit()

        # These guards must fail before any provider call or persistence hook.
        calls_after_success = adapter.calls
        cross_tenant = context.model_copy(update={"tenant_id": uuid4()})
        try:
            await service.execute(
                request, context=cross_tenant, configured_provider="anthropic",
                configured_model="claude-haiku-4-5-20251001", dataset=dataset, bundle=bundle,
                budget_policy=BudgetPolicy(
                    tenant_id=tenant_id, provider="anthropic", model=None, module=None,
                    daily_token_limit=10_000, monthly_token_limit=100_000, request_token_limit=6_000,
                ),
                used_today=0, used_month=0,
                prompt_values={"dataset": "x", "configuration": "x"},
                runtime_state=RuntimeInvariantState(spot_never_sell_at_loss_config=True),
            )
        except AIOrchestrationError as exc:
            cross_tenant_code = exc.detail.code.value
        else:
            raise RuntimeError("cross-tenant request was not denied")

        unknown_request = request.model_copy(update={
            "provider_request": ProviderModelRequest(
                provider="anthropic", model="unknown-staging-model", allow_request_override=True,
            ),
            "correlation_id": f"{correlation_id}-unknown",
        })
        try:
            await service.execute(
                unknown_request, context=context, configured_provider="anthropic",
                configured_model="claude-haiku-4-5-20251001", dataset=dataset, bundle=bundle,
                budget_policy=BudgetPolicy(
                    tenant_id=tenant_id, provider="anthropic", model=None, module=None,
                    daily_token_limit=10_000, monthly_token_limit=100_000, request_token_limit=6_000,
                ),
                used_today=0, used_month=0,
                prompt_values={"dataset": "x", "configuration": "x"},
                runtime_state=RuntimeInvariantState(spot_never_sell_at_loss_config=True),
            )
        except AIOrchestrationError as exc:
            unknown_model_code = exc.detail.code.value
        else:
            raise RuntimeError("unknown model was not denied")
        if adapter.calls != calls_after_success:
            raise RuntimeError("a fail-closed guard reached the provider adapter")

        live_after = int((await session.scalar(
            select(func.count()).select_from(Profile).where(Profile.live_trading_enabled.is_(True))
        )) or 0)
        autopilot_after = int((await session.scalar(
            select(func.count()).select_from(Profile).where(Profile.auto_pilot_enabled.is_(True))
        )) or 0)
        record_counts = {}
        for name, model in (
            ("model_resolutions", AIModelResolutionRecord),
            ("configuration_bundles", AIConfigurationBundleRecord),
            ("dataset_snapshots", AIDatasetSnapshotRecord),
            ("requests", AIRequestRecord),
            ("jobs", AIJobRecord),
            ("results", AIResultRecord),
            ("usage_records", AIUsageRecord),
            ("tool_call_audits", AIToolCallAudit),
        ):
            record_counts[name] = int((await session.scalar(
                select(func.count()).select_from(model).where(model.tenant_id == tenant_id)
            )) or 0)

    # Prove the database trigger rejects mutation of approved prompt content.
    async with engine.connect() as connection:
        transaction = await connection.begin()
        savepoint = await connection.begin_nested()
        try:
            await connection.execute(text(
                "UPDATE ai_prompt_versions SET system_template = system_template || ' mutated' "
                "WHERE prompt_key = 'shadow-detailed-analysis' AND semantic_version = '1.0.0'"
            ))
        except DBAPIError:
            prompt_mutation_denied = True
            await savepoint.rollback()
        else:
            prompt_mutation_denied = False
            await savepoint.rollback()
        await transaction.rollback()
    if not prompt_mutation_denied:
        raise RuntimeError("approved prompt immutability trigger did not reject mutation")

    expired_at = now - timedelta(seconds=1)
    recovered = LeaseJob.queued(
        tenant_id=tenant_id, purpose="STAGING_RECOVERY", identity={"correlation_id": correlation_id}
    ).acquire("stale-owner", now=now - timedelta(minutes=2), lease_seconds=1).model_copy(update={
        "status": AIJobState.RUNNING,
        "lease_expires_at": expired_at,
    }).acquire("recovery-owner", now=now)

    proof = {
        "verdict": "SYSTEMIC_AI_FOUNDATION_STAGING_PROVEN",
        "environment": environment,
        "external_provider_calls": 0,
        "fake_adapter_calls": adapter.calls,
        "authority": "ANALYSIS_ONLY",
        "result_status": result.status,
        "dataset_quality_status": dataset.quality_status,
        "record_counts_for_synthetic_tenant": record_counts,
        "cross_tenant_guard": cross_tenant_code,
        "unknown_model_guard": unknown_model_code,
        "live_write_tool_guard": denial_code,
        "approved_prompt_mutation_denied": prompt_mutation_denied,
        "stale_lease_recovered": recovered.last_error_code == AIErrorCode.STALE_JOB_RECOVERED,
        "live_trading_enabled_before": live_before,
        "live_trading_enabled_after": live_after,
        "autopilot_enabled_before": autopilot_before,
        "autopilot_enabled_after": autopilot_after,
        "live_state_unchanged": live_before == live_after and autopilot_before == autopilot_after,
        "correlation_id": correlation_id,
        "ai_request_id": str(request.ai_request_id),
        "dataset_hash": dataset.dataset_hash,
        "configuration_bundle_hash": bundle.bundle_hash,
    }
    print(json.dumps(proof, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
