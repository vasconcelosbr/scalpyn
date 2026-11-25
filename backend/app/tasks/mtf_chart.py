"""Independent observational worker. Never dispatches trading tasks."""
import logging

from .celery_app import celery_app


@celery_app.task(name="app.tasks.mtf_chart.observe")
def observe():
    from .compute_indicators import _run_async
    from ..services.mtf_chart_service import run_cycle
    return _run_async(run_cycle())


@celery_app.task(name='app.tasks.mtf_chart.advance_replays', acks_late=False,
                 soft_time_limit=240, time_limit=270)
def advance_replays():
    from .compute_indicators import _run_async
    from ..database import CeleryAsyncSessionLocal
    from ..services.mtf_signal_replay import advance_all
    from ..services.mtf_pump_flow import revise_all
    async def run():
        async with CeleryAsyncSessionLocal() as db:
            replays = await advance_all(db)
        # Own session: a flow revision failure never affects replay progress.
        revisions = None
        try:
            async with CeleryAsyncSessionLocal() as db:
                revisions = await revise_all(db)
        except Exception:
            logging.getLogger(__name__).exception('MTF Pump flow revision cycle failed')
        return {'replays': replays, 'pump_flow_revisions': revisions, 'operational_effect': False}
    return _run_async(run())
