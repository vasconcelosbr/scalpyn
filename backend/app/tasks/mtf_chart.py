"""Independent observational worker. Never dispatches trading tasks."""
from .celery_app import celery_app


@celery_app.task(name="app.tasks.mtf_chart.observe")
def observe():
    from .compute_indicators import _run_async
    from ..services.mtf_chart_service import run_cycle
    return _run_async(run_cycle())
