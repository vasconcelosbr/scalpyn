import logging
from .celery_app import celery_app
from .radar_auto_discover import _run_async

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.storage_retention.run")
def run(owner=None, force=False, preview=False):
    from ..services.storage_retention import scheduled_run
    result = _run_async(scheduled_run(owner, force=force, preview=preview))
    logger.info("[storage-retention] %s", result)
    return result
