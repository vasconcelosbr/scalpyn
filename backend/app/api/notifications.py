"""Notifications API — settings CRUD, Slack test."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Dict, Any
from uuid import UUID

from ..database import get_db
from ..models.notification import NotificationSetting
from ..services.notification_service import notification_service
from .config import get_current_user_id

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("/settings")
async def get_notification_settings(
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await db.execute(
        select(NotificationSetting).where(NotificationSetting.user_id == user_id)
    )
    settings = result.scalars().first()

    if not settings:
        return {"settings": _defaults()}

    return {"settings": _serialize(settings)}


@router.put("/settings")
async def update_notification_settings(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user_id: UUID = Depends(get_current_user_id),
):
    result = await db.execute(
        select(NotificationSetting).where(NotificationSetting.user_id == user_id)
    )
    settings = result.scalars().first()

    if not settings:
        settings = NotificationSetting(user_id=user_id)
        db.add(settings)

    for key in [
        "slack_webhook_url", "slack_enabled", "push_enabled", "email_enabled",
        "notify_on_buy", "notify_on_sell", "notify_on_stop_loss",
        "notify_on_take_profit", "notify_on_circuit_breaker",
        "daily_summary_enabled",
    ]:
        if key in payload:
            setattr(settings, key, payload[key])

    await db.commit()
    await db.refresh(settings)
    return {"status": "success", "settings": _serialize(settings)}


@router.post("/test-slack")
async def test_slack_webhook(
    payload: Dict[str, Any],
    user_id: UUID = Depends(get_current_user_id),
):
    webhook_url = payload.get("webhook_url", "")
    if not webhook_url:
        raise HTTPException(status_code=400, detail="webhook_url is required")

    success = await notification_service.test_slack(webhook_url)
    if success:
        return {"status": "success", "message": "Slack test message sent!"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send Slack test message")


def _serialize(s: NotificationSetting) -> dict:
    return {
        "id": str(s.id),
        "slack_webhook_url": s.slack_webhook_url or "",
        "slack_enabled": s.slack_enabled,
        "push_enabled": s.push_enabled,
        "email_enabled": s.email_enabled,
        "notify_on_buy": s.notify_on_buy,
        "notify_on_sell": s.notify_on_sell,
        "notify_on_stop_loss": s.notify_on_stop_loss,
        "notify_on_take_profit": s.notify_on_take_profit,
        "notify_on_circuit_breaker": s.notify_on_circuit_breaker,
        "daily_summary_enabled": s.daily_summary_enabled,
    }


def _defaults() -> dict:
    return {
        "slack_webhook_url": "",
        "slack_enabled": False,
        "push_enabled": False,
        "email_enabled": False,
        "notify_on_buy": True,
        "notify_on_sell": True,
        "notify_on_stop_loss": True,
        "notify_on_take_profit": True,
        "notify_on_circuit_breaker": True,
        "daily_summary_enabled": True,
    }
