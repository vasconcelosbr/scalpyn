"""Append-only audit events for Bayesian operations."""

from __future__ import annotations

import json
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def record_event(
    db: AsyncSession,
    *,
    user_id: UUID,
    profile_id: UUID,
    event_type: str,
    payload: Mapping[str, Any],
    actor_user_id: UUID | None = None,
    analysis_run_id: UUID | None = None,
    study_id: UUID | None = None,
    candidate_link_id: UUID | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
) -> UUID:
    event_id = uuid4()
    await db.execute(
        text(
            """
            INSERT INTO profile_bayesian_audit_events (
                id, user_id, actor_user_id, profile_id, analysis_run_id,
                study_id, candidate_link_id, event_type, previous_status,
                new_status, payload
            ) VALUES (
                :id, :user_id, :actor_user_id, :profile_id, :analysis_run_id,
                :study_id, :candidate_link_id, :event_type, :previous_status,
                :new_status, CAST(:payload AS JSONB)
            )
            """
        ),
        {
            "id": str(event_id),
            "user_id": str(user_id),
            "actor_user_id": str(actor_user_id) if actor_user_id else None,
            "profile_id": str(profile_id),
            "analysis_run_id": str(analysis_run_id) if analysis_run_id else None,
            "study_id": str(study_id) if study_id else None,
            "candidate_link_id": (
                str(candidate_link_id) if candidate_link_id else None
            ),
            "event_type": event_type,
            "previous_status": previous_status,
            "new_status": new_status,
            "payload": json.dumps(dict(payload), default=str),
        },
    )
    return event_id
