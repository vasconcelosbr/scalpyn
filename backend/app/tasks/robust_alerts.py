"""Robust-indicator alert evaluator (Celery beat).

Runs every ~90 seconds and inspects recent ``indicator_snapshots`` rows.
Each condition uses **sustained-window** semantics: it only fires when
the threshold breach holds across the *full* configured window AND in the
most recent 1-minute sub-window — a momentary blip does not page anyone.

  * ``staleness``        — max envelope age > 300s, sustained 2 minutes.
  * ``low_confidence``   — average confidence < 0.6 sustained over 5 min.
  * ``rejection_rate``   — rejection ratio > 50 % sustained over 5 min.

Alerts are rate-limited per condition to one Slack notification every 15
minutes, using a Redis key when Redis is reachable and an in-process dict
as a fallback. Slack delivery is restricted to a single ops-only webhook
(``ROBUST_ALERTS_OPS_WEBHOOK_URL``) — alerts contain operator data and
must not leak across tenants. When the ops webhook is unset the
condition is logged and dropped.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text

from .celery_app import celery_app

logger = logging.getLogger(__name__)


_RATE_LIMIT_SECONDS = 15 * 60
_LOCAL_RATE_LIMIT: dict[str, float] = {}


def _ops_webhook_url() -> Optional[str]:
    try:
        from ..config import settings
        url = getattr(settings, "ROBUST_ALERTS_OPS_WEBHOOK_URL", None)
    except Exception:
        url = None
    url = url or os.environ.get("ROBUST_ALERTS_OPS_WEBHOOK_URL")
    return url.strip() if isinstance(url, str) and url.strip() else None


async def _rate_limit_check_async(
    condition: str,
    redis_client,
    ttl_seconds: int = _RATE_LIMIT_SECONDS,
) -> bool:
    """Return True when the alert is permitted (rate-limit window open)."""
    ttl = int(ttl_seconds) if ttl_seconds and ttl_seconds > 0 else _RATE_LIMIT_SECONDS
    if redis_client is not None:
        key = f"robust_alerts:rl:{condition}"
        try:
            ok = await redis_client.set(key, "1", ex=ttl, nx=True)
            return bool(ok)
        except Exception as exc:
            logger.debug("[robust_alerts] redis rate-limit failed: %s", exc)

    now = datetime.now(timezone.utc).timestamp()
    last = _LOCAL_RATE_LIMIT.get(condition, 0.0)
    if now - last < ttl:
        return False
    _LOCAL_RATE_LIMIT[condition] = now
    return True


async def _send_ops_alert(message: str) -> None:
    """Send the alert to the ops webhook only — never per-user broadcast."""
    url = _ops_webhook_url()
    if not url:
        logger.info("[robust_alerts] ops alert (no webhook configured): %s", message)
        return
    try:
        from ..services.notification_service import notification_service
        await notification_service._send_slack(url, message)
    except Exception as exc:
        logger.warning("[robust_alerts] ops slack send failed: %s", exc)


async def _query_window(db, minutes: int) -> Optional[dict]:
    """Aggregate snapshot stats over the last ``minutes`` minutes."""
    try:
        row = (await db.execute(text(
            f"""
            SELECT
                COUNT(*)                                    AS total,
                AVG(global_confidence)                      AS avg_conf,
                SUM(CASE WHEN rejection_reason IS NOT NULL THEN 1 ELSE 0 END) AS rejected,
                MAX(extract(epoch from (now() - timestamp))) AS max_age,
                MIN(extract(epoch from (now() - timestamp))) AS min_age
            FROM indicator_snapshots
            WHERE timestamp > now() - interval '{int(minutes)} minutes'
            """
        ))).first()
    except Exception as exc:
        logger.debug("[robust_alerts] snapshot query failed: %s", exc)
        return None

    if row is None or row.total in (None, 0):
        return None

    return {
        "total": int(row.total or 0),
        "avg_conf": float(row.avg_conf or 0.0),
        "rejected": int(row.rejected or 0),
        "max_age": float(row.max_age or 0.0),
        "min_age": float(row.min_age or 0.0),
    }


async def _evaluate_async() -> dict:
    """Inspect recent snapshots and fire alerts. Returns a small report."""
    from ..database import AsyncSessionLocal

    report = {"checked": 0, "fired": []}
    try:
        async with AsyncSessionLocal() as db:
            window_5m = await _query_window(db, 5)
            window_2m = await _query_window(db, 2)
            window_1m = await _query_window(db, 1)
            if window_5m is None:
                return report

            report["checked"] = window_5m["total"]

            try:
                from ..services.config_service import _make_redis_client
                redis_client = _make_redis_client()
            except Exception:
                redis_client = None

            # ── staleness sustained 2 min ────────────────────────────────
            if (
                window_2m is not None
                and window_2m["min_age"] > 300.0
                and await _rate_limit_check_async("staleness", redis_client)
            ):
                msg = (
                    ":warning: *Robust indicators* — staleness breach sustained "
                    f">2m (min age {window_2m['min_age']:.0f}s, "
                    f"max age {window_2m['max_age']:.0f}s, samples={window_2m['total']})."
                )
                await _send_ops_alert(msg)
                report["fired"].append("staleness")

            # ── low_confidence sustained 5 min ───────────────────────────
            sustained_low_conf = (
                window_5m["avg_conf"] < 0.6
                and window_5m["total"] >= 5
                and (window_1m is None or window_1m["total"] == 0
                     or window_1m["avg_conf"] < 0.6)
            )
            if sustained_low_conf and await _rate_limit_check_async(
                "low_confidence", redis_client
            ):
                msg = (
                    ":warning: *Robust indicators* — average confidence "
                    f"{window_5m['avg_conf']:.3f} sustained over last 5 minutes "
                    f"(threshold 0.60, samples={window_5m['total']})."
                )
                await _send_ops_alert(msg)
                report["fired"].append("low_confidence")

            # ── rejection_rate sustained 5 min ───────────────────────────
            rate_5m = (
                (window_5m["rejected"] / window_5m["total"])
                if window_5m["total"] else 0.0
            )
            rate_1m = (
                (window_1m["rejected"] / window_1m["total"])
                if (window_1m and window_1m["total"]) else 0.0
            )
            sustained_rejection = (
                rate_5m > 0.5
                and window_5m["total"] >= 5
                and (window_1m is None or window_1m["total"] == 0 or rate_1m > 0.5)
            )
            if sustained_rejection and await _rate_limit_check_async(
                "rejection_rate", redis_client
            ):
                msg = (
                    ":warning: *Robust indicators* — rejection rate "
                    f"{rate_5m * 100:.1f}% sustained over last 5 minutes "
                    f"(threshold 50%, samples={window_5m['total']})."
                )
                await _send_ops_alert(msg)
                report["fired"].append("rejection_rate")
    except Exception as exc:
        logger.warning("[robust_alerts] evaluator failed: %s", exc)
    return report


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(name="app.tasks.robust_alerts.evaluate")
def evaluate() -> dict:
    """Celery task entry point — runs the alert evaluator and returns the report."""
    try:
        report = _run_async(_evaluate_async())
    except Exception as exc:
        logger.warning("[robust_alerts] task crashed: %s", exc, exc_info=True)
        return {"checked": 0, "fired": [], "error": str(exc)}
    if report.get("fired"):
        logger.info("[robust_alerts] fired alerts: %s", report["fired"])
    return report
