"""Periodic post-commit entry-risk capture and reconciliation."""

from __future__ import annotations

import asyncio

from .celery_app import celery_app
from ..database import run_db_task
from ..services.entry_risk_capture_service import capture_pending_entry_risk


@celery_app.task(name="app.tasks.entry_risk_capture.reconcile")
def reconcile(limit: int = 100) -> dict[str, int]:
    return asyncio.run(
        run_db_task(
            lambda db: capture_pending_entry_risk(db, limit=limit),
            celery=True,
        )
    )
