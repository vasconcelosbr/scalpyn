"""Reconcile one already-consumed staging provider request without retrying it."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import URL, select
from sqlalchemy.dialects.postgresql import insert


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True, type=UUID)
    parser.add_argument("--provider-request-id", required=True)
    parser.add_argument("--tokens-input", required=True, type=int)
    parser.add_argument("--tokens-output", required=True, type=int)
    return parser.parse_args()


async def main() -> None:
    args = _arguments()
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
    if "staging" not in environment.lower():
        raise SystemExit("REFUSED: provider usage reconciliation is staging-only")
    if args.tokens_input <= 0 or args.tokens_output <= 0:
        raise SystemExit("REFUSED: literal positive provider token counts are required")
    if not args.provider_request_id.startswith("req_"):
        raise SystemExit("REFUSED: provider request id is invalid")
    public_database_url = os.getenv("DATABASE_PUBLIC_URL")
    if public_database_url and ".railway.internal" not in public_database_url:
        os.environ["DATABASE_URL"] = public_database_url
    elif os.getenv("RAILWAY_TCP_PROXY_DOMAIN") and os.getenv("RAILWAY_TCP_PROXY_PORT"):
        os.environ["DATABASE_URL"] = URL.create(
            "postgresql+asyncpg",
            username=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
            host=os.environ["RAILWAY_TCP_PROXY_DOMAIN"],
            port=int(os.environ["RAILWAY_TCP_PROXY_PORT"]),
            database=os.environ["PGDATABASE"],
            query={"ssl": "require"},
        ).render_as_string(hide_password=False)

    from app.database import AsyncSessionLocal, engine
    import app.models  # noqa: F401
    from app.models.ai_graph import AIGraphEvent, AIGraphRun
    from app.models.ai_provider_key import AIProviderKey
    from app.models.systemic_ai import (
        AIBudgetPolicyRecord,
        AIModelApprovalRecord,
        AIModelResolutionRecord,
        AIRequestRecord,
        AIResultRecord,
        AIUsageRecord,
    )

    created = False
    async with AsyncSessionLocal() as session:
        async with session.begin():
            run = (await session.execute(select(AIGraphRun).where(
                AIGraphRun.id == args.run_id,
            ).with_for_update())).scalar_one_or_none()
            if run is None or run.status != "FAILED" or run.terminal_reason != "FAIL_CLOSED":
                raise RuntimeError("FAILED_CLOSED_STAGING_RUN_REQUIRED")

            request = await session.get(AIRequestRecord, run.ai_request_id)
            if request is None or request.tenant_id != run.tenant_id or request.authority != "ANALYSIS_ONLY":
                raise RuntimeError("ANALYSIS_ONLY_REQUEST_LINEAGE_INVALID")
            if (await session.scalar(select(AIResultRecord.id).where(
                AIResultRecord.tenant_id == run.tenant_id,
                AIResultRecord.ai_request_id == request.id,
            ))) is not None:
                raise RuntimeError("COMPLETED_RESULT_CONFLICT")

            resolution = await session.get(AIModelResolutionRecord, request.model_resolution_id)
            if resolution is None or resolution.configured_model != resolution.effective_model:
                raise RuntimeError("CONFIGURED_EFFECTIVE_MODEL_MISMATCH")
            approval_id = UUID(str((request.request_json or {})["frozen_context"]["model_approval_id"]))
            approval = await session.get(AIModelApprovalRecord, approval_id)
            if approval is None or approval.tenant_id != run.tenant_id:
                raise RuntimeError("MODEL_COST_APPROVAL_LINEAGE_INVALID")
            if approval.expires_at > datetime.now(timezone.utc):
                raise RuntimeError("MODEL_COST_APPROVAL_MUST_BE_EXPIRED")

            budget = (await session.execute(select(AIBudgetPolicyRecord).where(
                AIBudgetPolicyRecord.tenant_id == run.tenant_id,
                AIBudgetPolicyRecord.provider == resolution.effective_provider,
                AIBudgetPolicyRecord.model == resolution.effective_model,
                AIBudgetPolicyRecord.module == request.origin_module,
            ).with_for_update())).scalar_one_or_none()
            if budget is None or budget.is_active:
                raise RuntimeError("CANARY_BUDGET_MUST_BE_DISABLED")

            token_total = args.tokens_input + args.tokens_output
            actual_cost = (
                Decimal(args.tokens_input) * Decimal(approval.input_cost_per_million)
                + Decimal(args.tokens_output) * Decimal(approval.output_cost_per_million)
            ) / Decimal("1000000")
            if actual_cost > Decimal(approval.max_cost_usd):
                raise RuntimeError("PROVIDER_COST_EXCEEDS_HUMAN_APPROVAL")

            usage = (await session.execute(select(AIUsageRecord).where(
                AIUsageRecord.tenant_id == run.tenant_id,
                AIUsageRecord.ai_request_id == request.id,
            ).with_for_update())).scalar_one_or_none()
            if usage is None:
                key = (await session.execute(select(AIProviderKey).where(
                    AIProviderKey.user_id == run.tenant_id,
                    AIProviderKey.provider == resolution.effective_provider,
                    AIProviderKey.is_active.is_(True),
                    AIProviderKey.is_validated.is_(True),
                ).with_for_update())).scalar_one()
                usage = AIUsageRecord(
                    tenant_id=run.tenant_id,
                    ai_request_id=request.id,
                    provider=resolution.effective_provider,
                    model=resolution.effective_model,
                    module=request.origin_module,
                    tokens_input=args.tokens_input,
                    tokens_output=args.tokens_output,
                    estimated_cost=Decimal(approval.max_cost_usd),
                    actual_cost=actual_cost,
                    currency="USD",
                    pricing_snapshot_version=approval.pricing_snapshot_hash,
                )
                session.add(usage)
                key.tokens_used_month = int(key.tokens_used_month or 0) + token_total
                key.last_used_at = datetime.now(timezone.utc)
                created = True
            elif (
                usage.tokens_input != args.tokens_input
                or usage.tokens_output != args.tokens_output
                or Decimal(usage.actual_cost) != actual_cost
            ):
                raise RuntimeError("EXISTING_PROVIDER_USAGE_CONFLICT")

            await session.execute(insert(AIGraphEvent).values(
                tenant_id=run.tenant_id,
                graph_run_id=run.id,
                event_key=f"{run.id}:provider-usage-reconciled",
                event_type="PROVIDER_USAGE_RECONCILED",
                node_name="invoke_provider",
                status="COMPLETED",
                payload={
                    "provider_request_id": args.provider_request_id,
                    "tokens_input": args.tokens_input,
                    "tokens_output": args.tokens_output,
                    "actual_cost_usd": str(actual_cost),
                    "approval_max_cost_usd": str(approval.max_cost_usd),
                    "provider_retried": False,
                },
            ).on_conflict_do_nothing(
                index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key],
            ))

    print(json.dumps({
        "environment": environment,
        "run_id": str(args.run_id),
        "provider_request_id": args.provider_request_id,
        "tokens_input": args.tokens_input,
        "tokens_output": args.tokens_output,
        "actual_cost_usd": str(actual_cost),
        "usage_created": created,
        "provider_retried": False,
        "run_status": "FAILED",
        "terminal_reason": "FAIL_CLOSED",
    }, sort_keys=True, separators=(",", ":")))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
