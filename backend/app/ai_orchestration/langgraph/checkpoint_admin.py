"""Tenant-safe metadata inspection and retention-policy deletion CLI.

No automatic retention deletion is scheduled. Destruction requires an active
admin/superuser, an explicit policy approval identifier, a reason, and the
``--execute-delete`` switch. Canonical graph audit rows remain preserved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import select

from ...database import run_db_task
from ...models.ai_graph import AIGraphRun
from ...models.user import User
from .checkpoint import postgres_checkpointer


async def _authorized_run(db, *, tenant_id: UUID, thread_id: UUID, actor_id: UUID | None = None):
    run = (await db.execute(select(AIGraphRun).where(
        AIGraphRun.tenant_id == tenant_id,
        AIGraphRun.thread_id == thread_id,
    ))).scalar_one_or_none()
    if run is None:
        raise RuntimeError("CHECKPOINT_THREAD_NOT_FOUND")
    if actor_id:
        actor = await db.get(User, actor_id)
        if actor is None or not actor.is_active or (actor.role or "").lower() not in {"admin", "superuser"}:
            raise RuntimeError("CHECKPOINT_RETENTION_ACTOR_DENIED")
    return {
        "graph_run_id": str(run.id), "thread_id": str(run.thread_id),
        "tenant_id": str(run.tenant_id), "status": run.status,
        "current_node": run.current_node, "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


async def inspect_metadata(tenant_id: UUID, thread_id: UUID) -> dict:
    metadata = await run_db_task(
        lambda db: _authorized_run(db, tenant_id=tenant_id, thread_id=thread_id)
    )
    async with postgres_checkpointer() as saver:
        # The root graph can persist under the runtime's effective namespace
        # (currently the empty root namespace). Authorization is anchored to
        # the canonical tenant/thread row above, so enumerate every namespace
        # for that single authorized thread instead of assuming one.
        config = {"configurable": {"thread_id": str(thread_id)}}
        checkpoints = [item async for item in saver.alist(config, limit=100)]
    metadata["checkpoint_count"] = len(checkpoints)
    metadata["checkpoint_ids"] = [
        item.config.get("configurable", {}).get("checkpoint_id") for item in checkpoints
    ]
    metadata["checkpoint_namespaces"] = sorted({
        str(item.config.get("configurable", {}).get("checkpoint_ns", ""))
        for item in checkpoints
    })
    return metadata


async def list_threads(tenant_id: UUID) -> list[dict]:
    async def _list(db):
        rows = list((await db.execute(select(AIGraphRun).where(
            AIGraphRun.tenant_id == tenant_id,
        ).order_by(AIGraphRun.created_at.desc()).limit(500))).scalars().all())
        return [{
            "graph_run_id": str(row.id), "thread_id": str(row.thread_id),
            "status": row.status, "created_at": row.created_at.isoformat(),
        } for row in rows]
    return await run_db_task(_list)


async def delete_thread(
    tenant_id: UUID, thread_id: UUID, *, actor_id: UUID,
    policy_approval_id: str, reason: str, execute_delete: bool,
) -> dict:
    if not execute_delete or len(policy_approval_id.strip()) < 8 or len(reason.strip()) < 12:
        raise RuntimeError("CHECKPOINT_RETENTION_EXPLICIT_APPROVAL_REQUIRED")
    metadata = await run_db_task(lambda db: _authorized_run(
        db, tenant_id=tenant_id, thread_id=thread_id, actor_id=actor_id,
    ))
    async with postgres_checkpointer() as saver:
        await saver.adelete_thread(str(thread_id))
    return {
        **metadata, "checkpoint_deleted": True,
        "policy_approval_id": policy_approval_id, "reason": reason,
        "canonical_audit_preserved": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tenant-safe LangGraph checkpoint administration")
    parser.add_argument("action", choices=("list", "inspect", "delete"))
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--thread-id", type=UUID)
    parser.add_argument("--actor-id", type=UUID)
    parser.add_argument("--policy-approval-id")
    parser.add_argument("--reason")
    parser.add_argument("--execute-delete", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.action == "list":
        result = asyncio.run(list_threads(args.tenant_id))
    elif args.action == "inspect":
        if not args.thread_id:
            raise SystemExit("--thread-id is required")
        result = asyncio.run(inspect_metadata(args.tenant_id, args.thread_id))
    else:
        if not all((args.thread_id, args.actor_id, args.policy_approval_id, args.reason)):
            raise SystemExit("delete requires thread, actor, policy approval, and reason")
        result = asyncio.run(delete_thread(
            args.tenant_id, args.thread_id, actor_id=args.actor_id,
            policy_approval_id=args.policy_approval_id, reason=args.reason,
            execute_delete=args.execute_delete,
        ))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
