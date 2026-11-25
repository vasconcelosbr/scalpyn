"""Independent observational worker. Never dispatches trading tasks."""
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
    async def run():
        async with CeleryAsyncSessionLocal() as db:
            return {'replays': await advance_all(db), 'operational_effect': False}
    return _run_async(run())
