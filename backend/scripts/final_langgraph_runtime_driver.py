"""Zero-secret operator driver for the final staging crash/resume proof.

Run through ``railway run`` so the isolated staging variables are injected.
The script emits identifiers and counts only; it never prints credentials or
connection strings.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.ai_orchestration.langgraph.checkpoint_admin import inspect_metadata
from app.ai_orchestration.langgraph.staging_canary import (
    READONLY_CANARY_TOOLS,
    _assert_staging,
    _hash,
    _seed,
)
from app.ai_orchestration.module_registry import module_capability_registry
from app.database import run_db_task
from app.models.ai_graph import AIGraphInterrupt, AIGraphRun
from app.models.systemic_ai import (
    AIDatasetSnapshotRecord,
    AIRequestRecord,
    AIResultRecord,
    AIToolEvidenceRecord,
    AIUsageRecord,
)
from app.services.ai_graph_service import AIGraphRunService
from app.tasks.ai_orchestration import resume_graph_run, start_graph_run


ORIGIN_MODULES = (
    "strategy_profiles",
    "ml_models",
    "shadow_portfolio",
    "score_engine",
    "global_risk",
    "strategies",
    "social_score",
)


async def _seed_start() -> dict:
    password = _assert_staging()
    context = await run_db_task(lambda db: _seed(db, password))
    run_id = context["regenerative_run_a_id"]
    start_graph_run.apply_async(args=[str(run_id)], queue="ai_orchestration")
    return {
        "action": "SEED_START_QUEUED",
        "tenant_id": str(context["tenant_id"]),
        "run_id": str(run_id),
        "thread_id": str(context["regenerative_thread_a_id"]),
        "ai_request_id": str(context["regenerative_request_a_id"]),
        "dataset_id": str(context["dataset_id"]),
        "bundle_id": str(context["bundle_id"]),
    }


async def _snapshot_rows(db, run_id: UUID) -> dict:
    run = await db.get(AIGraphRun, run_id)
    if run is None:
        raise RuntimeError("GRAPH_RUN_NOT_FOUND")
    events = (await db.execute(text("""
        SELECT event_key, event_type, node_name, status
          FROM ai_graph_events
         WHERE tenant_id = :tenant_id AND graph_run_id = :run_id
         ORDER BY id
    """), {"tenant_id": run.tenant_id, "run_id": run.id})).mappings().all()
    interrupts = (await db.execute(select(AIGraphInterrupt).where(
        AIGraphInterrupt.tenant_id == run.tenant_id,
        AIGraphInterrupt.graph_run_id == run.id,
    ).order_by(AIGraphInterrupt.created_at))).scalars().all()
    side_effect_counts = {}
    for table_name in (
        "decision_hypotheses", "ai_change_sets", "experiment_links",
        "regeneration_runs", "decision_memory", "ai_tool_call_audits",
        "ai_tool_evidence", "ai_results", "ai_usage_records",
    ):
        side_effect_counts[table_name] = int((await db.execute(text(
            f"SELECT count(*) FROM {table_name} WHERE tenant_id = :tenant_id AND ai_request_id = :request_id"
        ), {"tenant_id": run.tenant_id, "request_id": run.ai_request_id})).scalar_one())
    order_count = int((await db.execute(text("""
        SELECT count(*) FROM orders
         WHERE user_id = :tenant_id AND created_at >= :created_at
    """), {"tenant_id": run.tenant_id, "created_at": run.created_at})).scalar_one())
    event_keys = [row["event_key"] for row in events]
    return {
        "run_id": str(run.id),
        "thread_id": str(run.thread_id),
        "tenant_id": str(run.tenant_id),
        "ai_request_id": str(run.ai_request_id),
        "status": run.status,
        "current_node": run.current_node,
        "terminal_reason": run.terminal_reason,
        "event_count": len(events),
        "event_key_duplicate_count": len(event_keys) - len(set(event_keys)),
        "completed_nodes": [row["node_name"] for row in events if row["event_type"] == "NODE_COMPLETED"],
        "interrupts": [{
            "id": str(item.id),
            "type": item.interrupt_type,
            "status": item.status,
            "decision": item.decision,
        } for item in interrupts],
        "side_effect_counts": side_effect_counts,
        "orders_created": order_count,
    }


async def _snapshot(run_id: UUID) -> dict:
    snapshot = await run_db_task(lambda db: _snapshot_rows(db, run_id))
    snapshot["checkpoint"] = await inspect_metadata(
        UUID(snapshot["tenant_id"]), UUID(snapshot["thread_id"])
    )
    snapshot["action"] = "SNAPSHOT"
    return snapshot


async def _resume_pending_record(db, run_id: UUID) -> dict:
    run = await db.get(AIGraphRun, run_id)
    if run is None:
        raise RuntimeError("GRAPH_RUN_NOT_FOUND")
    interrupt = (await db.execute(select(AIGraphInterrupt).where(
        AIGraphInterrupt.tenant_id == run.tenant_id,
        AIGraphInterrupt.graph_run_id == run.id,
        AIGraphInterrupt.status == "PENDING",
    ).order_by(AIGraphInterrupt.created_at))).scalars().first()
    if interrupt is None:
        raise RuntimeError("GRAPH_PENDING_INTERRUPT_NOT_FOUND")
    await AIGraphRunService.resume(
        db,
        tenant_id=run.tenant_id,
        actor_user_id=run.tenant_id,
        run_id=run.id,
        interrupt_id=interrupt.id,
        decision="approve",
        decision_id=uuid4(),
        idempotency_key=f"final-crash-resume-{uuid4()}",
        edits={},
    )
    return {"run_id": str(run.id), "interrupt_id": str(interrupt.id)}


async def _resume_pending(run_id: UUID) -> dict:
    record = await run_db_task(lambda db: _resume_pending_record(db, run_id))
    resume_graph_run.apply_async(
        args=[record["run_id"], record["interrupt_id"]],
        queue="ai_orchestration",
    )
    return {"action": "RESUME_QUEUED", **record}


async def _seed_origins_rows(db, password: str) -> dict:
    context = await _seed(db, password)
    now = datetime.now(timezone.utc)
    runs = {}
    for origin_module in ORIGIN_MODULES:
        capability = module_capability_registry[origin_module]
        manifest = {
            "modules_requested": [origin_module],
            "modules_consulted": [origin_module, *capability.dependencies],
            "tools_called": [],
            "freshness": {origin_module: capability.freshness_sla_seconds},
            "quality": {origin_module: "SYNTHETIC_STAGING_CANARY"},
            "evidence_ids": [f"synthetic-{origin_module}"],
        }
        dataset = AIDatasetSnapshotRecord(
            tenant_id=context["tenant_id"],
            contract_version="final-origin-canary-v1",
            origin_module=origin_module,
            module_context_refs={"profile_ids": [], "model_ids": [], "policy_ids": []},
            context_manifest=manifest,
            source_tables=["synthetic_staging_canary"],
            source_labels=[origin_module],
            event_identity_contract="final-origin-canary-event-v1",
            outcome_contract="analysis-only",
            time_anchor="observed_at",
            window_start=now - timedelta(minutes=1),
            window_end=now,
            filters={"environment": "staging", "origin_module": origin_module},
            exclusions=[],
            row_count=1,
            row_ids_hash=_hash([f"synthetic-{origin_module}"]),
            query_hash=_hash({"origin_module": origin_module, "tenant_scoped": True}),
            dataset_hash=_hash({"origin_module": origin_module, "row": "synthetic"}),
            configuration_bundle_id=context["bundle_id"],
            quality_status="PASS",
            quality_findings=[],
        )
        db.add(dataset)
        await db.flush()
        request_id = uuid4()
        request = AIRequestRecord(
            id=request_id,
            tenant_id=context["tenant_id"],
            requested_by_user_id=context["tenant_id"],
            origin_module=origin_module,
            origin_view=f"/final-origin-canary/{origin_module}",
            analysis_mode="SYSTEMIC",
            authority="ANALYSIS_ONLY",
            question_hash=_hash(f"final staging origin canary {origin_module}"),
            correlation_id=f"final-origin-{origin_module}-{request_id}",
            model_resolution_id=context["model_resolution_id"],
            prompt_version_id=context["prompt_id"],
            dataset_snapshot_id=dataset.id,
            configuration_bundle_id=context["bundle_id"],
            request_json={
                "request_intent": "FAKE_PROVIDER_CANARY",
                "staging_canary": True,
                "fake_provider": True,
                "origin_module": origin_module,
                "tool_allowlist": list(READONLY_CANARY_TOOLS),
                "frozen_context": {
                    "origin_module": origin_module,
                    "context_manifest": manifest,
                    "context_fingerprint": _hash({"origin_module": origin_module, "environment": "staging"}),
                    "mutation_fingerprint": _hash({"origin_module": origin_module, "authority": "ANALYSIS_ONLY"}),
                },
            },
        )
        db.add(request)
        await db.flush()
        db.add(AIResultRecord(
            tenant_id=context["tenant_id"],
            ai_request_id=request.id,
            status="COMPLETED",
            result_json={
                "diagnosis": f"Synthetic staging coverage for {origin_module}",
                "root_cause_classification": "INSUFFICIENT_EVIDENCE",
                "affected_modules": [origin_module],
                "evidence": [{"source": "synthetic_staging_canary", "origin_module": origin_module}],
                "data_quality": {"status": "PASS"},
                "market_regime": {"status": "NO_DATA"},
                "memory_hits": [],
                "recommendations": [],
                "warnings": [],
                "limitations": ["synthetic staging evidence; no production claim"],
            },
            terminal_reason="STAGING_ORIGIN_CANARY",
            completed_at=now,
        ))
        run = await AIGraphRunService.create(
            db,
            tenant_id=context["tenant_id"],
            user_id=context["tenant_id"],
            graph_key="systemic-analysis-v2",
            ai_request_id=request.id,
            idempotency_key=f"final-origin-run-{origin_module}-{request.id}",
        )
        runs[origin_module] = {
            "run_id": str(run.id),
            "thread_id": str(run.thread_id),
            "ai_request_id": str(request.id),
            "dataset_id": str(dataset.id),
        }
    return {"tenant_id": str(context["tenant_id"]), "runs": runs}


async def _origin_start() -> dict:
    password = _assert_staging()
    result = await run_db_task(lambda db: _seed_origins_rows(db, password))
    for item in result["runs"].values():
        start_graph_run.apply_async(args=[item["run_id"]], queue="ai_orchestration")
    return {"action": "ORIGIN_RUNS_QUEUED", **result}


async def _origin_snapshot_rows(db) -> dict:
    rows = (await db.execute(text("""
        SELECT DISTINCT ON (r.origin_module)
               r.origin_module, g.id AS run_id, g.thread_id, g.status, g.current_node,
               g.terminal_reason, g.created_at, r.id AS request_id,
               d.id AS dataset_id, d.origin_module AS dataset_origin_module,
               d.module_context_refs, d.context_manifest
          FROM ai_requests r
          JOIN ai_graph_runs g ON g.ai_request_id = r.id
          JOIN ai_dataset_snapshots d ON d.id = r.dataset_snapshot_id
         WHERE r.correlation_id LIKE 'final-origin-%'
         ORDER BY r.origin_module, g.created_at DESC
    """))).mappings().all()
    matrix = {}
    earliest = None
    tenant_id = None
    for row in rows:
        run = await db.get(AIGraphRun, row["run_id"])
        tenant_id = run.tenant_id
        earliest = row["created_at"] if earliest is None else min(earliest, row["created_at"])
        events = (await db.execute(text("""
            SELECT event_key, node_name FROM ai_graph_events
             WHERE tenant_id = :tenant_id AND graph_run_id = :run_id
        """), {"tenant_id": run.tenant_id, "run_id": run.id})).mappings().all()
        evidence = (await db.execute(select(AIToolEvidenceRecord).where(
            AIToolEvidenceRecord.tenant_id == run.tenant_id,
            AIToolEvidenceRecord.ai_request_id == run.ai_request_id,
        ).order_by(AIToolEvidenceRecord.created_at))).scalars().all()
        usage_count = int((await db.execute(select(text("count(*)")).select_from(AIUsageRecord).where(
            AIUsageRecord.tenant_id == run.tenant_id,
            AIUsageRecord.ai_request_id == run.ai_request_id,
        ))).scalar_one())
        event_keys = [item["event_key"] for item in events]
        matrix[row["origin_module"]] = {
            "run_id": str(run.id),
            "thread_id": str(run.thread_id),
            "status": run.status,
            "current_node": run.current_node,
            "terminal_reason": run.terminal_reason,
            "dataset_id": str(row["dataset_id"]),
            "dataset_origin_module": row["dataset_origin_module"],
            "module_context_refs": row["module_context_refs"],
            "context_manifest": row["context_manifest"],
            "event_count": len(events),
            "event_key_duplicate_count": len(event_keys) - len(set(event_keys)),
            "tool_evidence": [{
                "module": item.module_key,
                "tool": item.tool_name,
                "quality": item.quality,
                "output_hash": item.output_hash,
            } for item in evidence],
            "usage_count": usage_count,
        }
    orders_created = None
    if tenant_id is not None and earliest is not None:
        orders_created = int((await db.execute(text("""
            SELECT count(*) FROM orders
             WHERE user_id = :tenant_id AND created_at >= :created_at
        """), {"tenant_id": tenant_id, "created_at": earliest})).scalar_one())
    return {
        "action": "ORIGIN_SNAPSHOT",
        "origin_count": len(matrix),
        "completed_count": sum(item["status"] == "COMPLETED" for item in matrix.values()),
        "orders_created": orders_created,
        "matrix": matrix,
    }


async def _origin_snapshot() -> dict:
    _assert_staging()
    return await run_db_task(_origin_snapshot_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=("seed-start", "snapshot", "resume", "origin-start", "origin-snapshot"),
    )
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.action == "seed-start":
        result = asyncio.run(_seed_start())
    elif args.action == "origin-start":
        result = asyncio.run(_origin_start())
    elif args.action == "origin-snapshot":
        result = asyncio.run(_origin_snapshot())
    else:
        if not args.run_id:
            parser.error("--run-id is required")
        run_id = UUID(args.run_id)
        result = asyncio.run(_snapshot(run_id) if args.action == "snapshot" else _resume_pending(run_id))
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
