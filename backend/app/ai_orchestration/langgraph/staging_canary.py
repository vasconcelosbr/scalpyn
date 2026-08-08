"""Isolated, zero-cost staging canaries for systemic and regenerative graphs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from uuid import UUID, uuid4

from sqlalchemy import select

from ...api.auth import pwd_context
from ...database import run_db_task
from ...models.ai_graph import AIGraphInterrupt
from ...models.profile import Profile
from ...models.systemic_ai import (
    AIConfigurationBundleRecord, AIDatasetSnapshotRecord, AIModelResolutionRecord,
    AIPromptVersion, AIRequestRecord, AIResultRecord,
)
from ...models.user import User
from ...services.ai_graph_service import AIGraphRunService
from ...tasks.ai_orchestration import execute_graph_run
from .config import get_langgraph_settings


CANARY_EMAIL = "langgraph-canary@staging.scalpyn.com.br"


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _assert_staging() -> str:
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower()
    if "staging" not in environment:
        raise RuntimeError("LANGGRAPH_CANARY_STAGING_ONLY")
    if os.getenv("LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "false").lower() != "true":
        raise RuntimeError("LANGGRAPH_FAKE_PROVIDER_CANARY_DISABLED")
    password = os.getenv("LANGGRAPH_STAGING_CANARY_PASSWORD", "")
    if len(password) < 20:
        raise RuntimeError("LANGGRAPH_STAGING_CANARY_PASSWORD_REQUIRED")
    settings = get_langgraph_settings()
    settings.require_runtime()
    if not settings.entrypoints_enabled or not settings.regenerative_shadow_enabled:
        raise RuntimeError("LANGGRAPH_STAGING_CANARY_FLAGS_REQUIRED")
    if settings.real_provider_canary_enabled:
        raise RuntimeError("LANGGRAPH_REAL_PROVIDER_CANARY_MUST_REMAIN_DISABLED")
    return password


async def _seed(db, password: str) -> dict:
    user = (await db.execute(select(User).where(User.email == CANARY_EMAIL))).scalar_one_or_none()
    if user is None:
        user = User(
            id=uuid4(), email=CANARY_EMAIL, password_hash=pwd_context.hash(password),
            name="LangGraph Staging Canary", role="admin", is_active=True,
        )
        db.add(user)
        await db.flush()
    else:
        user.password_hash = pwd_context.hash(password)
        user.role = "admin"
        user.is_active = True

    candidate_config = {
        "default_timeframe": "5m",
        "scoring": {"weights": {"evidence": 1}, "thresholds": {"minimum": 1}, "selected_rule_ids": []},
        "signals": {"logic": "AND", "conditions": []},
        "live_trading_enabled": False,
    }
    score_config = candidate_config["scoring"]
    profile = (await db.execute(select(Profile).where(
        Profile.user_id == user.id, Profile.name == "LangGraph Canary Shadow",
    ))).scalar_one_or_none()
    if profile is None:
        profile = Profile(
            user_id=user.id, name="LangGraph Canary Shadow", description="Isolated staging canary",
            config=candidate_config, is_active=False, is_shadow_only=True,
            live_trading_enabled=False, auto_pilot_enabled=False,
        )
        db.add(profile)
        await db.flush()

    resolution = AIModelResolutionRecord(
        tenant_id=user.id, requested_provider="fake", requested_model="fake-analysis-v1",
        configured_provider="fake", configured_model="fake-analysis-v1",
        effective_provider="fake", effective_model="fake-analysis-v1",
        catalog_snapshot_hash=_hash({"fake": "staging-only"}),
        capabilities=["text", "structured_output"],
        resolution_policy_version="staging-fake-v1", resolution_reason="isolated_zero_cost_canary",
    )
    db.add(resolution)
    prompt = (await db.execute(select(AIPromptVersion).where(
        AIPromptVersion.prompt_key == "ai-critic", AIPromptVersion.status == "APPROVED",
    ))).scalar_one()
    bundle_payload = {"profile_id": str(profile.id), "config": candidate_config, "live_write": False}
    bundle = AIConfigurationBundleRecord(
        tenant_id=user.id, profile_id=profile.id, lineage_refs={"source": "staging_canary"},
        bundle_json=bundle_payload, bundle_hash=_hash(bundle_payload), lineage_status="COMPLETE",
    )
    db.add(bundle)
    await db.flush()
    now = datetime.now(timezone.utc)
    dataset = AIDatasetSnapshotRecord(
        tenant_id=user.id, contract_version="staging-canary-v1",
        source_tables=["synthetic_staging_canary"], source_labels=["FAKE_ADAPTER"],
        event_identity_contract="canary_event_id", outcome_contract="analysis_only",
        time_anchor="observed_at", window_start=now - timedelta(minutes=5), window_end=now,
        filters={"environment": "staging"}, exclusions=[], row_count=1,
        row_ids_hash=_hash(["canary-row-1"]), query_hash=_hash({"query": "synthetic"}),
        dataset_hash=_hash({"row": "canary-row-1", "environment": "staging"}),
        configuration_bundle_id=bundle.id, quality_status="PASS", quality_findings=[],
    )
    db.add(dataset)
    await db.flush()

    async def make_request(mode: str, authority: str, suffix: str) -> AIRequestRecord:
        request_id = uuid4()
        payload = {
            "staging_canary": True, "fake_provider": True,
            "candidate_config": candidate_config, "score_config": score_config,
            "mutation_reason": "staging_canary_shadow_only",
        }
        record = AIRequestRecord(
            id=request_id, tenant_id=user.id, requested_by_user_id=user.id,
            origin_module="LANGGRAPH_STAGING_CANARY", origin_view="/intelligence-runs",
            analysis_mode=mode, authority=authority, question_hash=_hash("staging canary"),
            correlation_id=f"langgraph-staging-{suffix}-{request_id}",
            model_resolution_id=resolution.id, prompt_version_id=prompt.id,
            dataset_snapshot_id=dataset.id, configuration_bundle_id=bundle.id,
            request_json=payload,
        )
        db.add(record)
        await db.flush()
        return record

    analysis_request = await make_request("SYSTEMIC", "ANALYSIS_ONLY", "analysis")
    result_payload = {
        "ai_request_id": str(analysis_request.id), "status": "COMPLETED",
        "tenant_id": str(user.id), "provider": "fake", "requested_model": "fake-analysis-v1",
        "configured_model": "fake-analysis-v1", "effective_model": "fake-analysis-v1",
        "model_resolution_id": str(resolution.id), "prompt_version_id": str(prompt.id),
        "prompt_hash": prompt.content_hash, "dataset_snapshot_id": str(dataset.id),
        "dataset_hash": dataset.dataset_hash, "configuration_bundle_id": str(bundle.id),
        "configuration_bundle_hash": bundle.bundle_hash,
        "analysis": {"verdict": "STAGING_FAKE_ADAPTER_CANARY", "live_write": False},
        "recommendations": [], "evidence_refs": [], "tool_calls": [],
        "usage": {
            "tokens_input": 0, "tokens_output": 0, "estimated_cost": str(Decimal("0")),
            "actual_cost": str(Decimal("0")), "currency": "USD",
            "pricing_snapshot_version": "ZERO_COST_FAKE_STAGING", "reservation": str(Decimal("0")),
            "limit": str(Decimal("0")), "remaining": str(Decimal("0")),
        },
        "warnings": [], "limitations": ["isolated fake adapter; no provider claim"],
        "terminal_reason": "STAGING_CANARY", "completed_at": now.isoformat(),
    }
    db.add(AIResultRecord(
        tenant_id=user.id, ai_request_id=analysis_request.id, status="COMPLETED",
        result_json=result_payload, terminal_reason="STAGING_CANARY", completed_at=now,
    ))
    regenerative_request = await make_request("REGENERATIVE", "SHADOW_ONLY", "regenerative")
    analysis_run = await AIGraphRunService.create(
        db, tenant_id=user.id, user_id=user.id, graph_key="systemic-analysis-v1",
        ai_request_id=analysis_request.id, idempotency_key=f"canary-analysis-{analysis_request.id}",
    )
    regenerative_run = await AIGraphRunService.create(
        db, tenant_id=user.id, user_id=user.id, graph_key="regenerative-shadow-v1",
        ai_request_id=regenerative_request.id, idempotency_key=f"canary-regenerative-{regenerative_request.id}",
    )
    return {
        "tenant_id": user.id, "analysis_run_id": analysis_run.id,
        "regenerative_run_id": regenerative_run.id, "dataset_id": dataset.id,
        "bundle_id": bundle.id, "prompt_id": prompt.id, "model_resolution_id": resolution.id,
        "profile_id": profile.id,
    }


async def _pending_interrupt(db, run_id: UUID) -> AIGraphInterrupt:
    return (await db.execute(select(AIGraphInterrupt).where(
        AIGraphInterrupt.graph_run_id == run_id,
        AIGraphInterrupt.status == "PENDING",
    ).order_by(AIGraphInterrupt.created_at.desc()))).scalars().first()


async def _resolve(db, context: dict, interrupt_id: UUID, decision: str = "approve"):
    return await AIGraphRunService.resume(
        db, tenant_id=context["tenant_id"], actor_user_id=context["tenant_id"],
        run_id=context["regenerative_run_id"], interrupt_id=interrupt_id,
        decision=decision, decision_id=uuid4(), idempotency_key=f"canary-resume-{uuid4()}", edits={},
    )


async def run_canaries() -> dict:
    password = _assert_staging()
    context = await run_db_task(lambda db: _seed(db, password))
    analysis = await execute_graph_run(context["analysis_run_id"])
    first = await execute_graph_run(context["regenerative_run_id"])
    stages = [first]
    for _ in range(3):
        interrupt = await run_db_task(lambda db: _pending_interrupt(db, context["regenerative_run_id"]))
        if interrupt is None:
            break
        await run_db_task(lambda db, iid=interrupt.id: _resolve(db, context, iid))
        stage = await execute_graph_run(
            context["regenerative_run_id"],
            resume_payload={"decision": "approve", "edits": {}},
        )
        stages.append(stage)
    final_status = stages[-1]["status"] if stages else "NOT_RUN"
    if analysis["status"] != "COMPLETED" or final_status != "COMPLETED":
        raise RuntimeError("LANGGRAPH_STAGING_CANARY_INCOMPLETE")
    return {
        "status": "COMPLETED", "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME"),
        "analysis": analysis, "regenerative_stages": stages,
        **{key: str(value) for key, value in context.items()},
        "provider": "fake", "configured_model": "fake-analysis-v1",
        "effective_model": "fake-analysis-v1", "cost_usd": "0",
        "authority": ["ANALYSIS_ONLY", "SHADOW_ONLY"], "live_write": False,
        "real_provider_canary": "NOT_RUN_REQUIRES_COST_APPROVAL",
    }


def main() -> None:
    print(json.dumps(asyncio.run(run_canaries()), sort_keys=True))


if __name__ == "__main__":
    main()
