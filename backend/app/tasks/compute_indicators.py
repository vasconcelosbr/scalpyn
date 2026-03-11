from ..tasks.celery_app import celery_app
import logging

logger = logging.getLogger(__name__)

@celery_app.task(name="app.tasks.compute_indicators.compute")
def compute():
    logger.info("Executing compute_indicators task...")
    # 1. Read OHLCV from TimescaleDB
    # 2. Read 'indicators' config from config_service
    # 3. Calculate metrics using feature_engine
    # 4. Save to TimescaleDB 'indicators' hypertable
    # 5. Trigger compute_scores
    celery_app.send_task("app.tasks.compute_scores.score")
    return "Indicators computation initiated"
