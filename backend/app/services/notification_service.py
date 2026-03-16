"""Notification Service — sends alerts via Slack, email, push."""

import logging
from typing import Dict, Any, Optional
from uuid import UUID
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.notification import NotificationSetting

logger = logging.getLogger(__name__)


class NotificationService:
    """Sends trading notifications through configured channels."""

    async def send_trade_alert(
        self, db: AsyncSession, user_id: UUID, event_type: str, data: Dict[str, Any]
    ) -> None:
        """Send a trade-related alert to all enabled channels.

        event_type: 'buy', 'sell', 'stop_loss', 'take_profit', 'circuit_breaker'
        """
        settings = await self._get_settings(db, user_id)
        if not settings:
            return

        # Check if this event type should trigger notification
        event_enabled_map = {
            "buy": settings.notify_on_buy,
            "sell": settings.notify_on_sell,
            "stop_loss": settings.notify_on_stop_loss,
            "take_profit": settings.notify_on_take_profit,
            "circuit_breaker": settings.notify_on_circuit_breaker,
        }

        if not event_enabled_map.get(event_type, False):
            return

        message = self._format_message(event_type, data)

        if settings.slack_enabled and settings.slack_webhook_url:
            await self._send_slack(settings.slack_webhook_url, message)

        if settings.email_enabled:
            await self._send_email(user_id, event_type, message)

    async def send_daily_summary(
        self, db: AsyncSession, user_id: UUID, summary_data: Dict[str, Any]
    ) -> None:
        """Send daily P&L summary."""
        settings = await self._get_settings(db, user_id)
        if not settings or not settings.daily_summary_enabled:
            return

        message = self._format_daily_summary(summary_data)

        if settings.slack_enabled and settings.slack_webhook_url:
            await self._send_slack(settings.slack_webhook_url, message)

    async def test_slack(self, webhook_url: str) -> bool:
        """Test Slack webhook connectivity."""
        try:
            import httpx
            payload = {
                "text": ":white_check_mark: *Scalpyn* — Slack integration test successful!",
                "username": "Scalpyn Bot",
                "icon_emoji": ":chart_with_upwards_trend:",
            }
            async with httpx.AsyncClient() as client:
                r = await client.post(webhook_url, json=payload, timeout=10)
                return r.status_code == 200
        except Exception as e:
            logger.error(f"Slack test failed: {e}")
            return False

    async def _get_settings(self, db: AsyncSession, user_id: UUID) -> Optional[NotificationSetting]:
        result = await db.execute(
            select(NotificationSetting).where(NotificationSetting.user_id == user_id)
        )
        return result.scalars().first()

    async def _send_slack(self, webhook_url: str, message: str) -> None:
        try:
            import httpx
            payload = {
                "text": message,
                "username": "Scalpyn Bot",
                "icon_emoji": ":chart_with_upwards_trend:",
            }
            async with httpx.AsyncClient() as client:
                await client.post(webhook_url, json=payload, timeout=10)
        except Exception as e:
            logger.error(f"Slack notification failed: {e}")

    async def _send_email(self, user_id: UUID, subject: str, body: str) -> None:
        """Email sending placeholder — integrate with SendGrid/SES in production."""
        logger.info(f"Email notification queued for user {user_id}: {subject}")

    def _format_message(self, event_type: str, data: Dict[str, Any]) -> str:
        symbol = data.get("symbol", "?")
        price = data.get("price", 0)
        pnl = data.get("profit_loss", 0)

        emoji_map = {
            "buy": ":green_circle:",
            "sell": ":red_circle:",
            "stop_loss": ":octagonal_sign:",
            "take_profit": ":trophy:",
            "circuit_breaker": ":rotating_light:",
        }
        emoji = emoji_map.get(event_type, ":bell:")

        if event_type == "buy":
            return f"{emoji} *BUY {symbol}* @ ${price:,.2f} | Score: {data.get('score', '?')}"
        elif event_type == "sell":
            return f"{emoji} *SELL {symbol}* @ ${price:,.2f} | P&L: ${pnl:,.2f}"
        elif event_type == "stop_loss":
            return f"{emoji} *STOP LOSS HIT* {symbol} @ ${price:,.2f} | Loss: ${pnl:,.2f}"
        elif event_type == "take_profit":
            return f"{emoji} *TAKE PROFIT HIT* {symbol} @ ${price:,.2f} | Profit: ${pnl:,.2f}"
        elif event_type == "circuit_breaker":
            return f"{emoji} *CIRCUIT BREAKER ACTIVATED* — Trading halted. Daily loss limit reached."
        return f"{emoji} {event_type}: {symbol} @ ${price:,.2f}"

    def _format_daily_summary(self, data: Dict[str, Any]) -> str:
        total_pnl = data.get("total_pnl", 0)
        trades_count = data.get("trades_count", 0)
        win_rate = data.get("win_rate", 0)
        emoji = ":chart_with_upwards_trend:" if total_pnl >= 0 else ":chart_with_downwards_trend:"

        return (
            f"{emoji} *Scalpyn Daily Summary* — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            f"• P&L: {'$' if total_pnl >= 0 else '-$'}{abs(total_pnl):,.2f}\n"
            f"• Trades: {trades_count}\n"
            f"• Win Rate: {win_rate:.1f}%\n"
            f"• Open Positions: {data.get('open_positions', 0)}"
        )


notification_service = NotificationService()
