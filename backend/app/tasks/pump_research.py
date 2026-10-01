"""Celery tasks — Pump Monitor research dataset: offline labelling and partition upkeep.

Run on the research worker (``research_ohlcv`` queue), never on the Pump
Monitor worker, so labelling can never delay the 30 s observation cycle.
"""
import logging

from ..tasks.celery_app import celery_app
from .radar_auto_discover import _run_async

logger = logging.getLogger(__name__)


async def _research_spec(db):
    from ..services.pump_monitor_service import _enabled_configs
    configs = await _enabled_configs(db)
    return configs[0][1]["research"] if configs else None


@celery_app.task(name="app.tasks.pump_research.label")
def label():
    from ..database import run_db_task
    from ..services import pump_research

    async def _run():
        spec = await run_db_task(_research_spec, celery=True)
        if not spec or not spec.get("enabled"):
            return {"skipped": "research_disabled"}
        total, last = 0, {}
        # Idempotent batches (one transaction each) until the eligible backlog is drained.
        while True:
            last = await run_db_task(lambda db: pump_research.label_pending(db, spec), celery=True)
            total += last["labelled"]
            if last["labelled"] < int(spec["labels"]["batch_rows"]):
                break
        return {**last, "labelled": total}
    return _run_async(_run())


@celery_app.task(name="app.tasks.pump_research.maintain")
def maintain():
    from ..database import run_db_task
    from ..services import pump_research

    async def _run(db):
        spec = await _research_spec(db)
        if not spec:
            return {"skipped": "not_configured"}
        return await pump_research.maintain(db, spec)
    return _run_async(run_db_task(_run, celery=True))
