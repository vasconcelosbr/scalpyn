from ..tasks.celery_app import celery_app
import logging

logger = logging.getLogger(__name__)

@celery_app.task(name="app.tasks.compute_scores.score")
def score():
    logger.info("Executing compute_scores task...")
    # 1. Read indicators
    # 2. Read 'score' config
    # 3. Calculate Alpha Score using score_engine
    # 4. Save to TimescaleDB 'alpha_scores'
    # 5. Trigger evaluate_signals
    celery_app.send_task("app.tasks.evaluate_signals.evaluate")
    return "Score computation initiated"
