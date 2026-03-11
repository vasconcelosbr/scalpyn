from ..tasks.celery_app import celery_app
import logging

logger = logging.getLogger(__name__)

@celery_app.task(name="app.tasks.collect_market_data.collect_all")
def collect_all():
    logger.info("Executing collect_all task: Fetching data from exchanges...")
    # 1. Fetch from exchanges
    # 2. Store in TimescaleDB OHLCV
    # 3. Trigger compute_indicators
    celery_app.send_task("app.tasks.compute_indicators.compute")
    return "Market data collection initiated"
