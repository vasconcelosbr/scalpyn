"""Isolated, zero-cost staging canaries for systemic and regenerative graphs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from uuid import UUID, uuid4

from sqlalchemy import select, text

from ...api.auth import pwd_context
from ...database import run_db_task
from ...models.ai_graph import AIGraphEvent, AIGraphInterrupt
from ...models.profile import Profile
from ...models.systemic_ai import (
    AIConfigurationBundleRecord, AIDatasetSnapshotRecord, AIModelResolutionRecord,
    AIPromptVersion, AIRequestRecord, AIResultRecord, AIToolEvidenceRecord,
)
from ...models.user import User
from ...services.ai_graph_service import AIGraphRunService
from ...tasks.ai_orchestration import execute_graph_run
from .config import get_langgraph_settings


CANARY_EMAIL = "langgraph-canary@staging.scalpyn.com.br"

READONLY_CANARY_TOOLS = (
    "strategy_profiles.get_profile",
    "ml_models.get_authority_status",
    "shadow.get_performance_summary",
    "score_engine.get_effective_configuration_at",
    "global_risk.validate_recommendation",
    "strategies.validate_recommendation",
    "intelligence_runs.list_runs",
    "social_score.get_snapshot",
    "market_regime.get_current",
    "audit_memory.find_similar_decisions",
)


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
        AIPromptVersion.prompt_key == "systemic-multimodule",
        AIPromptVersion.status == "APPROVED",
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
    canary_context_id = str(uuid4())

    async def make_request(
        mode: str, authority: str, suffix: str, *, market_regime: str = "synthetic",
    ) -> AIRequestRecord:
        request_id = uuid4()
        context = {
            "canary_context_id": canary_context_id,
            "profile_family": "staging-canary",
            "timeframe": "5m",
            "market_regime": market_regime,
            "social_regime": "missing",
            "risk_policy_version": "staging-readonly-v1",
            "strategy_exit_policy": "never-sell-at-loss",
            "feature_contract": "unchanged",
            "label_contract": "unchanged",
            "model_lane": "none",
        }
        mutation = {
            "origin_module": "shadow_portfolio",
            "analysis_mode": mode,
            "target": "profile.candidate.shadow_only",
        }
        payload = {
            "staging_canary": True, "fake_provider": True,
            "candidate_config": candidate_config, "score_config": score_config,
            "mutation_reason": "staging_canary_shadow_only",
            "tool_allowlist": list(READONLY_CANARY_TOOLS),
            "dataset_request": {
                "entity_ids": [str(profile.id)],
                "filters": {"max_rows": 20, "staging_canary": True},
            },
            "frozen_context": {
                "origin_module": "shadow_portfolio",
                "entity_ids": [str(profile.id)],
                **context,
                "context": context,
                "context_fingerprint": _hash(context),
                "mutation_fingerprint": _hash(mutation),
                "proposed_changes": [{
                    "target_module": "strategy_profiles",
                    "target_path": "profile.candidate.shadow_only",
                    "side_effect_class": "CANDIDATE_WRITE",
                }],
                "global_risk_veto": False,
                "strategy_invariant_conflict": False,
            },
        }
        record = AIRequestRecord(
            id=request_id, tenant_id=user.id, requested_by_user_id=user.id,
            origin_module="shadow_portfolio", origin_view="/intelligence-runs",
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
    regenerative_request_a = await make_request("REGENERATIVE", "SHADOW_ONLY", "regenerative-a")
    regenerative_request_b = await make_request("REGENERATIVE", "SHADOW_ONLY", "regenerative-b")
    regenerative_request_c = await make_request(
        "REGENERATIVE", "SHADOW_ONLY", "regenerative-c", market_regime="synthetic-change",
    )
    analysis_run = await AIGraphRunService.create(
        db, tenant_id=user.id, user_id=user.id, graph_key="systemic-analysis-v2",
        ai_request_id=analysis_request.id, idempotency_key=f"canary-analysis-{analysis_request.id}",
    )
    regenerative_runs = []
    for label, request in (
        ("a", regenerative_request_a),
        ("b", regenerative_request_b),
        ("c", regenerative_request_c),
    ):
        regenerative_runs.append(await AIGraphRunService.create(
            db, tenant_id=user.id, user_id=user.id, graph_key="regenerative-shadow-v2",
            ai_request_id=request.id, idempotency_key=f"canary-regenerative-{label}-{request.id}",
        ))
    return {
        "tenant_id": user.id, "analysis_run_id": analysis_run.id,
        "analysis_thread_id": analysis_run.thread_id,
        "analysis_request_id": analysis_request.id,
        "regenerative_run_id": regenerative_runs[0].id,
        "regenerative_run_a_id": regenerative_runs[0].id,
        "regenerative_run_b_id": regenerative_runs[1].id,
        "regenerative_run_c_id": regenerative_runs[2].id,
        "regenerative_thread_a_id": regenerative_runs[0].thread_id,
        "regenerative_thread_b_id": regenerative_runs[1].thread_id,
        "regenerative_thread_c_id": regenerative_runs[2].thread_id,
        "regenerative_request_a_id": regenerative_request_a.id,
        "regenerative_request_b_id": regenerative_request_b.id,
        "regenerative_request_c_id": regenerative_request_c.id,
        "context_fingerprint_ab": regenerative_request_a.request_json["frozen_context"]["context_fingerprint"],
        "context_fingerprint_c": regenerative_request_c.request_json["frozen_context"]["context_fingerprint"],
        "mutation_fingerprint": regenerative_request_a.request_json["frozen_context"]["mutation_fingerprint"],
        "canary_started_at": now,
        "dataset_id": dataset.id,
        "bundle_id": bundle.id, "prompt_id": prompt.id, "model_resolution_id": resolution.id,
        "profile_id": profile.id,
    }


async def _pending_interrupt(db, run_id: UUID) -> AIGraphInterrupt:
    return (await db.execute(select(AIGraphInterrupt).where(
        AIGraphInterrupt.graph_run_id == run_id,
        AIGraphInterrupt.status == "PENDING",
    ).order_by(AIGraphInterrupt.created_at.desc()))).scalars().first()


async def _resolve(
    db, context: dict, run_id: UUID, interrupt_id: UUID, decision: str = "approve",
):
    return await AIGraphRunService.resume(
        db, tenant_id=context["tenant_id"], actor_user_id=context["tenant_id"],
        run_id=run_id, interrupt_id=interrupt_id,
        decision=decision, decision_id=uuid4(), idempotency_key=f"canary-resume-{uuid4()}", edits={},
    )


async def _drive_regenerative(context: dict, run_id: UUID) -> list[dict]:
    stages = [await execute_graph_run(run_id)]
    for _ in range(3):
        interrupt = await run_db_task(lambda db: _pending_interrupt(db, run_id))
        if interrupt is None:
            break
        await run_db_task(lambda db, iid=interrupt.id: _resolve(db, context, run_id, iid))
        stages.append(await execute_graph_run(
            run_id, resume_payload={"decision": "approve", "edits": {}},
        ))
    return stages


async def _runtime_proof(db, context: dict) -> dict:
    run_ids = [
        context["regenerative_run_a_id"], context["regenerative_run_b_id"],
        context["regenerative_run_c_id"],
    ]
    events = list((await db.execute(select(AIGraphEvent).where(
        AIGraphEvent.tenant_id == context["tenant_id"],
        AIGraphEvent.graph_run_id.in_(run_ids),
        AIGraphEvent.node_name.in_((
            "retrieve_contextual_memory", "create_profile_candidate_version",
        )),
    ).order_by(AIGraphEvent.graph_run_id, AIGraphEvent.id))).scalars())
    by_run: dict[str, dict] = {str(run_id): {} for run_id in run_ids}
    for event in events:
        by_run[str(event.graph_run_id)][str(event.node_name)] = dict(event.payload or {})
    memories = (await db.execute(text("""
        SELECT id, ai_request_id, status, mutation_fingerprint, context_fingerprint
          FROM decision_memory
         WHERE tenant_id = :tenant_id
           AND ai_request_id = ANY(CAST(:request_ids AS uuid[]))
         ORDER BY created_at
    """), {
        "tenant_id": context["tenant_id"],
        "request_ids": [
            str(context["regenerative_request_a_id"]),
            str(context["regenerative_request_b_id"]),
            str(context["regenerative_request_c_id"]),
        ],
    })).mappings().all()
    memory_by_request = {str(row["ai_request_id"]): str(row["id"]) for row in memories}
    memory_a_id = memory_by_request.get(str(context["regenerative_request_a_id"]))
    memory_b_hits = (
        by_run[str(context["regenerative_run_b_id"])]
        .get("retrieve_contextual_memory", {})
        .get("decision_memory_ids", [])
    )
    memory_c_hits = (
        by_run[str(context["regenerative_run_c_id"])]
        .get("retrieve_contextual_memory", {})
        .get("decision_memory_ids", [])
    )
    tool_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
        AIToolEvidenceRecord.tenant_id == context["tenant_id"],
        AIToolEvidenceRecord.ai_request_id == context["analysis_request_id"],
    ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id))).scalars())
    order_count = int((await db.execute(text("""
        SELECT count(*) FROM orders
         WHERE user_id = :tenant_id AND created_at >= :started_at
    """), {
        "tenant_id": context["tenant_id"], "started_at": context["canary_started_at"],
    })).scalar_one())
    return {
        "tool_evidence_count": len(tool_rows),
        "tool_evidence": [{
            "id": str(row.id), "module": row.module_key, "tool": row.tool_name,
            "output_hash": row.output_hash, "quality": row.quality,
        } for row in tool_rows],
        "regenerative_events": by_run,
        "decision_memory": [{key: str(value) if value is not None else None for key, value in row.items()}
                            for row in memories],
        "run_b_reused_run_a": bool(memory_a_id and memory_a_id in memory_b_hits),
        "run_b_memory_hit_ids": memory_b_hits,
        "run_c_different_context_memory_hit_ids": memory_c_hits,
        "run_c_avoided_global_block": len(memory_c_hits) == 0,
        "orders_created_during_canary": order_count,
    }


async def run_canaries() -> dict:
    password = _assert_staging()
    context = await run_db_task(lambda db: _seed(db, password))
    analysis = await execute_graph_run(context["analysis_run_id"])
    stages_a = await _drive_regenerative(context, context["regenerative_run_a_id"])
    stages_b = await _drive_regenerative(context, context["regenerative_run_b_id"])
    stages_c = await _drive_regenerative(context, context["regenerative_run_c_id"])
    final_statuses = [stages[-1]["status"] if stages else "NOT_RUN" for stages in (
        stages_a, stages_b, stages_c,
    )]
    runtime_proof = await run_db_task(lambda db: _runtime_proof(db, context))
    from .checkpoint_admin import inspect_metadata
    checkpoint_proof = {
        "analysis": await inspect_metadata(context["tenant_id"], context["analysis_thread_id"]),
        "run_a": await inspect_metadata(context["tenant_id"], context["regenerative_thread_a_id"]),
        "run_b": await inspect_metadata(context["tenant_id"], context["regenerative_thread_b_id"]),
        "run_c": await inspect_metadata(context["tenant_id"], context["regenerative_thread_c_id"]),
    }
    failures = []
    if analysis["status"] != "COMPLETED":
        failures.append("ANALYSIS")
    if any(status != "COMPLETED" for status in final_statuses):
        failures.append("REGENERATIVE")
    if not runtime_proof["run_b_reused_run_a"]:
        failures.append("MEMORY_REUSE")
    if not runtime_proof["run_c_avoided_global_block"]:
        failures.append("MEMORY_CONTEXT_ISOLATION")
    if runtime_proof["orders_created_during_canary"] != 0:
        failures.append("ORDER_RECONCILIATION")
    if any(item["checkpoint_count"] < 1 for item in checkpoint_proof.values()):
        failures.append("CHECKPOINT")
    if failures:
        raise RuntimeError("LANGGRAPH_STAGING_CANARY_INCOMPLETE:" + ",".join(failures))
    return {
        "status": "COMPLETED", "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME"),
        "analysis": analysis, "regenerative_stages": stages_a,
        "regenerative_runs": {
            "run_a": {"run_id": str(context["regenerative_run_a_id"]), "stages": stages_a},
            "run_b": {"run_id": str(context["regenerative_run_b_id"]), "stages": stages_b},
            "run_c": {"run_id": str(context["regenerative_run_c_id"]), "stages": stages_c},
        },
        "runtime_proof": runtime_proof,
        "checkpoint_proof": checkpoint_proof,
        **{key: str(value) for key, value in context.items() if key != "canary_started_at"},
        "provider": "fake", "configured_model": "fake-analysis-v1",
        "effective_model": "fake-analysis-v1", "cost_usd": "0",
        "authority": ["ANALYSIS_ONLY", "SHADOW_ONLY"], "live_write": False,
        "real_provider_canary": "NOT_RUN_REQUIRES_COST_APPROVAL",
    }


def main() -> None:
    print(json.dumps(asyncio.run(run_canaries()), sort_keys=True))


if __name__ == "__main__":
    main()
