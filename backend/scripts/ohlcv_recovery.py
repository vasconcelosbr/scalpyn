"""Operator CLI. plan/status are read-only; submit/enqueue require explicit actions."""
import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID
from app.schemas.ohlcv_recovery import RecoveryManifest


async def execute(args):
    from app.database import CeleryAsyncSessionLocal, _celery_engine
    from app.services import ohlcv_recovery as service
    try:
        async with CeleryAsyncSessionLocal() as db:
            if args.action in ('plan', 'submit'):
                manifest = RecoveryManifest.model_validate_json(Path(args.manifest).read_text(encoding='utf-8'))
                if args.action == 'plan':
                    return await service.plan(db, args.owner, manifest)
                return {'job_id': await service.submit(db, args.owner, manifest, args.sha256)}
            result = await service.status(db, args.owner, args.job)
            if args.action == 'status':
                return result
            cfg = await service.load_config(db, args.owner)
            if not cfg.enabled:
                raise ValueError('Recovery is disabled')
            if result['status'] in ('COMPLETE', 'EXHAUSTED'):
                return {'status': result['status'], 'enqueued': False}
            # End the read transaction before contacting Redis/the broker.
            await db.rollback()
            from app.tasks.task_dispatch import enqueue
            task_id = enqueue('app.tasks.ohlcv_recovery.recover',
                              dedup_key=f'ohlcv-recovery:{args.job}',
                              ttl_seconds=cfg.dispatch_ttl_seconds,
                              args=(str(args.job),))
            return {'job_id': str(args.job), 'task_id': task_id}
    finally:
        await _celery_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', type=UUID, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('plan', 'submit'):
        command = sub.add_parser(action)
        command.add_argument('--manifest', required=True)
        if action == 'submit':
            command.add_argument('--sha256', required=True)
    for action in ('status', 'enqueue'):
        sub.add_parser(action).add_argument('--job', type=UUID, required=True)
    print(json.dumps(asyncio.run(execute(parser.parse_args())), default=str, indent=2))


if __name__ == '__main__':
    main()
