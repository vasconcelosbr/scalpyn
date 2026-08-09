"""Zero-secret operator driver for the final staging crash/resume proof.

Run through ``railway run`` so the isolated staging variables are injected.
The script emits identifiers and counts only; it never prints credentials or
connection strings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID, uuid4

from sqlalchemy import select, text

from app.ai_orchestration.langgraph.checkpoint_admin import inspect_metadata
from app.ai_orchestration.langgraph.staging_canary import _assert_staging, _seed
from app.database import run_db_task
from app.models.ai_graph import AIGraphInterrupt, AIGraphRun
from app.services.ai_graph_service import AIGraphRunService
from app.tasks.ai_orchestration import resume_graph_run, start_graph_run


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed-start", "snapshot", "resume"))
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.action == "seed-start":
        result = asyncio.run(_seed_start())
    else:
        if not args.run_id:
            parser.error("--run-id is required")
        run_id = UUID(args.run_id)
        result = asyncio.run(_snapshot(run_id) if args.action == "snapshot" else _resume_pending(run_id))
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
