"""Pump-only reserved research lane. No beat schedule or deployed consumer by default."""
from ..tasks.celery_app import celery_app
from .radar_auto_discover import _run_async


@celery_app.task(name="app.tasks.pump_ml.train_challenger")
def train_challenger(user_id,experiment_spec):
    from uuid import UUID
    from ..database import run_db_task
    from ..services.pump_opportunity_service import get_config
    from ..services.pump_opportunity_engine import validate_config

    async def check():
        c=await run_db_task(lambda db:get_config(db,UUID(user_id)),celery=True)
        validate_config(c)
        return {"status":"blocked","reason":"pump_training_budget_and_support_require_approval",
                "training_enabled":c["training_enabled"],"queue":"pump_ml","applied_delta":0}
    return _run_async(check())
