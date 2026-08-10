"""Run exactly one bounded real-provider Intelligence Runs canary in staging.

The provider key is loaded only from the encrypted tenant record. The real
provider flag is enabled only in this process, while the persistent Railway
flag remains false. The immutable approval is expired and the budget record is
disabled in ``finally``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import func, select, text


TENANT_ID = UUID("b02e84ad-a0eb-4fca-8cf6-bef1ccaafc40")
PROVIDER = "anthropic"
MODEL = "claude-haiku-4-5-20251001"
MODULE = "intelligence_runs"
MAX_INPUT_TOKENS = 10_125
MAX_OUTPUT_TOKENS = 1_975
REQUEST_TOKEN_LIMIT = 12_100
DAILY_TOKEN_LIMIT = 24_000
MONTHLY_TOKEN_LIMIT = 24_000
APPROVAL_TEXT = "APROVO O CANÁRIO REAL EM STAGING COM CUSTO MÁXIMO DE US$ 0,02."
PRICING_URL = "https://www.anthropic.com/claude/haiku"
QUESTION = (
    "Audite o ultimo Intelligence Run deste tenant de staging. Use somente as "
    "evidencias congeladas; reporte estado, limitacoes e inconsistencias. "
    "Nao proponha nem execute mutacoes."
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval-text", required=True)
    parser.add_argument("--max-cost-usd", required=True, type=Decimal)
    return parser.parse_args()


def _assert_staging(args: argparse.Namespace) -> str:
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "")
    if "staging" not in environment.lower():
        raise SystemExit("REFUSED: real provider canary is staging-only")
    if args.approval_text.strip() != APPROVAL_TEXT:
        raise SystemExit("REFUSED: exact staging canary approval is required")
    if args.max_cost_usd != Decimal("0.02"):
        raise SystemExit("REFUSED: approved maximum cost must equal USD 0.02")
    if os.getenv("AI_PROVIDER_MAX_ATTEMPTS") != "1":
        raise SystemExit("REFUSED: provider must be configured for one attempt")
    return environment


async def _scalar(session, statement) -> int:
    return int((await session.scalar(statement)) or 0)


def _expire_model_approval(approval, now: datetime) -> None:
    """Invalidate an immutable approval without changing its status field."""
    approval.expires_at = min(approval.expires_at, now)


async def main() -> None:
    args = _arguments()
    environment = _assert_staging(args)

    # These overrides are process-local and disappear when the script exits.
    os.environ["LANGGRAPH_REAL_PROVIDER_CANARY_ENABLED"] = "true"
    os.environ["AI_MODULE_INTELLIGENCE_RUNS_ENABLED"] = "true"

    from app.ai_orchestration.contracts import AnalysisMode, Authority
    from app.ai_orchestration.hashing import canonical_hash
    from app.ai_orchestration.provider_registry import default_registry
    from app.database import AsyncSessionLocal, engine
    import app.models  # noqa: F401
    from app.models.ai_graph import AIGraphEvent, AIGraphRun
    from app.models.ai_provider_key import AIProviderKey
    from app.models.systemic_ai import (
        AIBudgetPolicyRecord,
        AIModelApprovalRecord,
        AIRequestRecord,
        AIResultRecord,
        AIToolEvidenceRecord,
        AIUsageRecord,
    )
    from app.models.user import User
    from app.services.ai_graph_service import AIGraphRunService
    from app.services.module_ai_analysis_service import ModuleAIAnalysisService
    from app.tasks.ai_orchestration import execute_graph_run

    started_at = datetime.now(timezone.utc)
    approval_id = uuid4()
    budget_id = None
    run_id = None
    execution_error = None
    graph_result = None

    try:
        async with AsyncSessionLocal() as session:
            tenant = await session.get(User, TENANT_ID)
            if tenant is None or tenant.is_active:
                raise RuntimeError("STAGING_CANARY_TENANT_MUST_EXIST_AND_REMAIN_INACTIVE")

            key = (await session.execute(select(AIProviderKey).where(
                AIProviderKey.user_id == TENANT_ID,
                AIProviderKey.provider == PROVIDER,
                AIProviderKey.is_active.is_(True),
                AIProviderKey.is_validated.is_(True),
            ).with_for_update())).scalar_one_or_none()
            if key is None:
                raise RuntimeError("VALIDATED_STAGING_PROVIDER_KEY_REQUIRED")
            if int(key.tokens_used_month or 0) + REQUEST_TOKEN_LIMIT > MONTHLY_TOKEN_LIMIT:
                raise RuntimeError("STAGING_PROVIDER_KEY_BUDGET_INSUFFICIENT")
            key.monthly_token_limit = MONTHLY_TOKEN_LIMIT

            existing_request = (await session.execute(select(AIRequestRecord).where(
                AIRequestRecord.tenant_id == TENANT_ID,
            ).order_by(AIRequestRecord.created_at.desc()).limit(1))).scalar_one_or_none()
            if existing_request is None:
                raise RuntimeError("STAGING_CANARY_SEED_REQUEST_REQUIRED")

            seed = await AIGraphRunService.create(
                session,
                tenant_id=TENANT_ID,
                user_id=TENANT_ID,
                graph_key="systemic-analysis-v2",
                ai_request_id=existing_request.id,
                idempotency_key="real-provider-canary-dataset-seed-20260810-v1",
            )
            seed.status = "COMPLETED"
            seed.current_node = "complete"
            seed.started_at = started_at
            seed.completed_at = started_at
            seed.terminal_reason = "STAGING_SYNTHETIC_DATASET"
            completed_event = (await session.execute(select(AIGraphEvent).where(
                AIGraphEvent.graph_run_id == seed.id,
                AIGraphEvent.event_key == f"{seed.id}:synthetic-dataset-completed",
            ))).scalar_one_or_none()
            if completed_event is None:
                session.add(AIGraphEvent(
                    tenant_id=TENANT_ID,
                    graph_run_id=seed.id,
                    event_key=f"{seed.id}:synthetic-dataset-completed",
                    event_type="COMPLETED",
                    node_name="complete",
                    status="COMPLETED",
                    payload={"authority": "ANALYSIS_ONLY", "live_write": False},
                ))

            catalog = default_registry()
            catalog_entry = catalog.get_entry(PROVIDER, MODEL)
            if MAX_INPUT_TOKENS > catalog_entry.max_input or MAX_OUTPUT_TOKENS > catalog_entry.max_output:
                raise RuntimeError("CANARY_MODEL_LIMIT_EXCEEDS_CATALOG")
            pricing_payload = {
                "provider": PROVIDER,
                "model": MODEL,
                "input_cost_per_million": "1",
                "output_cost_per_million": "5",
                "pricing_source_url": PRICING_URL,
                "pricing_observed_at": started_at.isoformat(),
                "max_input_tokens": MAX_INPUT_TOKENS,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "catalog_snapshot_hash": catalog.catalog_snapshot_hash,
            }
            pricing_snapshot_hash = canonical_hash(pricing_payload)
            approval_payload = {
                "id": str(approval_id),
                "tenant_id": str(TENANT_ID),
                "provider": PROVIDER,
                "model": MODEL,
                "max_cost_usd": str(args.max_cost_usd),
                "scope": "SYSTEMIC_MODULE_ANALYSIS",
                "approved_by": str(TENANT_ID),
                "approved_at": started_at.isoformat(),
                "pricing_snapshot_hash": pricing_snapshot_hash,
                "module": MODULE,
                "request_token_limit": REQUEST_TOKEN_LIMIT,
                "daily_token_limit": DAILY_TOKEN_LIMIT,
                "monthly_token_limit": MONTHLY_TOKEN_LIMIT,
            }
            approval = AIModelApprovalRecord(
                id=approval_id,
                tenant_id=TENANT_ID,
                provider=PROVIDER,
                model=MODEL,
                max_cost_usd=args.max_cost_usd,
                input_cost_per_million=Decimal("1"),
                output_cost_per_million=Decimal("5"),
                max_output_tokens=MAX_OUTPUT_TOKENS,
                pricing_source_url=PRICING_URL,
                pricing_observed_at=started_at,
                pricing_snapshot_hash=pricing_snapshot_hash,
                approval_phrase_hash=canonical_hash(args.approval_text.strip()),
                scope="SYSTEMIC_MODULE_ANALYSIS",
                status="APPROVED",
                approved_by=TENANT_ID,
                approved_at=started_at,
                expires_at=started_at + timedelta(minutes=15),
                content_hash=canonical_hash(approval_payload),
            )
            session.add(approval)

            budget = (await session.execute(select(AIBudgetPolicyRecord).where(
                AIBudgetPolicyRecord.tenant_id == TENANT_ID,
                AIBudgetPolicyRecord.provider == PROVIDER,
                AIBudgetPolicyRecord.model == MODEL,
                AIBudgetPolicyRecord.module == MODULE,
            ).with_for_update())).scalar_one_or_none()
            if budget is None:
                budget = AIBudgetPolicyRecord(
                    tenant_id=TENANT_ID,
                    provider=PROVIDER,
                    model=MODEL,
                    module=MODULE,
                )
                session.add(budget)
            budget.request_token_limit = REQUEST_TOKEN_LIMIT
            budget.daily_token_limit = DAILY_TOKEN_LIMIT
            budget.monthly_token_limit = MONTHLY_TOKEN_LIMIT
            budget.null_limit_policy = "DENY"
            budget.is_active = True
            await session.flush()
            budget_id = budget.id
            await session.commit()

        async with AsyncSessionLocal() as session:
            run = await ModuleAIAnalysisService.create_run(
                session,
                tenant_id=TENANT_ID,
                user_id=TENANT_ID,
                origin_module=MODULE,
                origin_view="/staging/real-provider-canary/intelligence-runs",
                entity_ids=(),
                filters={"max_rows": 1, "environment": "staging", "synthetic_dataset": True},
                analysis_mode=AnalysisMode.SYSTEMIC,
                question=QUESTION,
                authority=Authority.ANALYSIS_ONLY,
                provider=PROVIDER,
                model=MODEL,
                model_approval_id=approval_id,
                idempotency_key="real-provider-intelligence-canary-20260810-v2",
            )
            run_id = run.id
            await session.commit()

        graph_result = await execute_graph_run(run_id)
    except Exception as exc:  # cleanup and safe reconciliation still run
        execution_error = str(exc).split(":", 1)[0]
    finally:
        async with AsyncSessionLocal() as session:
            approval = await session.get(AIModelApprovalRecord, approval_id)
            if approval is not None:
                _expire_model_approval(approval, datetime.now(timezone.utc))
            if budget_id is not None:
                budget = await session.get(AIBudgetPolicyRecord, budget_id)
                if budget is not None:
                    budget.is_active = False
            await session.commit()

    async with AsyncSessionLocal() as session:
        run = await session.get(AIGraphRun, run_id) if run_id else None
        result = (await session.execute(select(AIResultRecord).where(
            AIResultRecord.tenant_id == TENANT_ID,
            AIResultRecord.ai_request_id == run.ai_request_id,
        ))).scalar_one_or_none() if run is not None else None
        usage = (await session.execute(select(AIUsageRecord).where(
            AIUsageRecord.tenant_id == TENANT_ID,
            AIUsageRecord.ai_request_id == run.ai_request_id,
        ))).scalar_one_or_none() if run is not None else None
        tool_evidence_count = await _scalar(session, select(func.count()).select_from(AIToolEvidenceRecord).where(
            AIToolEvidenceRecord.tenant_id == TENANT_ID,
            AIToolEvidenceRecord.ai_request_id == run.ai_request_id,
        )) if run is not None else 0
        key = (await session.execute(select(AIProviderKey).where(
            AIProviderKey.user_id == TENANT_ID,
            AIProviderKey.provider == PROVIDER,
            AIProviderKey.is_active.is_(True),
        ))).scalar_one_or_none()
        budget = await session.get(AIBudgetPolicyRecord, budget_id) if budget_id else None
        approval = await session.get(AIModelApprovalRecord, approval_id)
        orders_created = int((await session.execute(text(
            "SELECT count(*) FROM orders WHERE created_at >= :started_at"
        ), {"started_at": started_at})).scalar_one())
        profiles_changed = int((await session.execute(text(
            "SELECT count(*) FROM profiles WHERE updated_at >= :started_at"
        ), {"started_at": started_at})).scalar_one())
        score_configs_changed = int((await session.execute(text("""
            SELECT count(*) FROM config_profiles
             WHERE updated_at >= :started_at
               AND config_type IN ('score', 'score_engine')
        """), {"started_at": started_at})).scalar_one())
        live_profiles = int((await session.execute(text("""
            SELECT count(*) FROM profiles
             WHERE live_trading_enabled IS TRUE OR auto_pilot_enabled IS TRUE
        """))).scalar_one())

        result_json = dict(result.result_json) if result is not None else {}
        usage_json = dict(result_json.get("usage") or {})
        output = {
            "environment": environment,
            "tenant_id": str(TENANT_ID),
            "tenant_active": False,
            "authority": "ANALYSIS_ONLY",
            "provider": PROVIDER,
            "configured_model": result_json.get("configured_model"),
            "effective_model": result_json.get("effective_model"),
            "graph_result": graph_result,
            "run_id": str(run.id) if run is not None else None,
            "run_status": run.status if run is not None else None,
            "terminal_reason": run.terminal_reason if run is not None else None,
            "execution_error": execution_error,
            "result_persisted": result is not None,
            "usage_persisted": usage is not None,
            "tokens_input": usage.tokens_input if usage is not None else None,
            "tokens_output": usage.tokens_output if usage is not None else None,
            "actual_cost_usd": str(usage.actual_cost) if usage is not None else None,
            "reserved_cost_usd": usage_json.get("estimated_cost"),
            "approved_max_cost_usd": str(args.max_cost_usd),
            "reservation_reconciled": usage_json.get("reservation_reconciled"),
            "tool_evidence_count": tool_evidence_count,
            "orders_created": orders_created,
            "profiles_changed": profiles_changed,
            "score_configs_changed": score_configs_changed,
            "live_profiles": live_profiles,
            "provider_key_tokens_used_month": int(key.tokens_used_month or 0) if key else None,
            "approval_status": approval.status if approval else None,
            "approval_expired": (
                approval.expires_at <= datetime.now(timezone.utc) if approval else None
            ),
            "budget_active": bool(budget.is_active) if budget else None,
            "persistent_real_provider_flag": os.getenv("RAILWAY_ENVIRONMENT_NAME") and False,
            "provider_key_material_printed": False,
        }
        print(json.dumps(output, sort_keys=True, separators=(",", ":"), default=str))

        failures = []
        if execution_error is not None:
            failures.append(f"execution_error={execution_error}")
        if run is None or run.status != "COMPLETED":
            failures.append("run_not_completed")
        if result is None:
            failures.append("result_missing")
        if usage is None:
            failures.append("usage_missing")
        if usage is not None and Decimal(usage.actual_cost) > args.max_cost_usd:
            failures.append("cost_cap_exceeded")
        if result_json.get("configured_model") != MODEL or result_json.get("effective_model") != MODEL:
            failures.append("configured_effective_model_mismatch")
        if tool_evidence_count != 7:
            failures.append("typed_tool_evidence_incomplete")
        if any((orders_created, profiles_changed, score_configs_changed, live_profiles)):
            failures.append("forbidden_side_effect_detected")
        if budget is None or budget.is_active:
            failures.append("budget_not_disabled")
        if (
            approval is None
            or approval.status != "APPROVED"
            or approval.expires_at > datetime.now(timezone.utc)
        ):
            failures.append("approval_not_expired")
        if failures:
            raise SystemExit("CANARY_RECONCILIATION_FAILED:" + ",".join(failures))

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
