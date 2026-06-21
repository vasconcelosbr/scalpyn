"""Audit log for Profile Intelligence Engine events."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from ..models.profile_intelligence import ProfileIntelligenceAuditLog

logger = logging.getLogger(__name__)


async def log_pi_event(
    db: AsyncSession,
    user_id: UUID,
    event_type: str,
    event_description: str = "",
    run_id: Optional[UUID] = None,
    suggestion_id: Optional[UUID] = None,
    combination_id: Optional[UUID] = None,
    payload_json: Optional[Dict] = None,
    result_json: Optional[Dict] = None,
    model_provider: Optional[str] = None,
    model_name: Optional[str] = None,
    prompt_text: Optional[str] = None,
    response_text: Optional[str] = None,
    before_json: Optional[Dict] = None,
    after_json: Optional[Dict] = None,
    diff_json: Optional[Dict] = None,
    actor_user_id: Optional[UUID] = None,
    profile_name: Optional[str] = None,
    source_run_id: Optional[UUID] = None,
) -> None:
    """Insert a row into profile_intelligence_audit_log. Never raises."""
    try:
        row = ProfileIntelligenceAuditLog(
            user_id=user_id,
            run_id=run_id,
            suggestion_id=suggestion_id,
            combination_id=combination_id,
            event_type=event_type,
            event_description=event_description,
            payload_json=payload_json,
            result_json=result_json,
            model_provider=model_provider,
            model_name=model_name,
            prompt_text=prompt_text,
            response_text=response_text,
            before_json=before_json,
            after_json=after_json,
            diff_json=diff_json,
            actor_user_id=actor_user_id,
            profile_name=profile_name,
            source_run_id=source_run_id,
        )
        db.add(row)
        await db.flush()
    except Exception as exc:
        logger.warning("[PIAudit] log_pi_event failed: %s", exc)
