"""Robust-indicator alert evaluator (Celery beat).

Runs every ~90 seconds and inspects recent ``indicator_snapshots`` rows.
Each condition uses **sustained-window** semantics: it only fires when
the threshold breach holds across the *full* configured window AND in the
most recent 1-minute sub-window — a momentary blip does not page anyone.

  * ``staleness``        — max envelope age > 300s, sustained 2 minutes
                           (i.e. the freshest snapshot in the 2-min
                           window is already older than 300s).
  * ``low_confidence``   — average confidence < 0.6 AND the most recent
                           1-min average is also < 0.6, sustained over a
                           5-minute window (≥ 5 samples).
  * ``rejection_rate``   — rejection ratio > 50 % over 5 min AND > 50 %
                           in the most recent 1-min sub-window.
  * ``divergence``       — `>10 %` bucket share above
                           ``ROBUST_ALERT_DIVERGENCE_PCT`` over 5 min
                           AND in the most recent 1-min sub-window.

Phase 3 (deprecation) adds a separate **hourly standby check** —
``check_legacy_rollback_standby`` — that asserts the emergency
``LEGACY_PIPELINE_ROLLBACK`` flag is False in production. The check
records the first time the flag is seen as True in Redis and pages
ops if the rollback stays True for more than 24 hours. The check runs
hourly via the ``robust_indicator_legacy_rollback_check`` beat entry
so a single failed beat tick still leaves us inside the 24-hour SLA.

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
    """Return True when the alert is permitted (rate-limit window open).

    ``ttl_seconds`` is per-call so callers can register a different
    window for low-frequency alerts (e.g. the 6-hour window used by the
    Phase 3 legacy-rollback standby check) without mutating any global
    state. Defaults to the 15-minute window used by the high-frequency
    sustained-condition alerts.
    """
    ttl = int(ttl_seconds) if ttl_seconds and ttl_seconds > 0 else _RATE_LIMIT_SECONDS
    if redis_client is not None:
        key = f"robust_alerts:rl:{condition}"
        try:
            ok = await redis_client.set(
                key, "1", ex=ttl, nx=True
            )
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
    """Send the alert to the ops webhook only — never per-user broadcast.

    Cross-tenant safety: divergence/rejection/confidence statistics are
    operator-only data and may not be sent to per-user Slack workspaces.
    When the ops webhook is unset we log the alert at INFO and drop it.
    """
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
                SUM(CASE WHEN divergence_bucket = '>10%' THEN 1 ELSE 0 END) AS bad_divergence,
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
        "bad_divergence": int(row.bad_divergence or 0),
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

            divergence_threshold_pct = float(
                os.environ.get("ROBUST_ALERT_DIVERGENCE_PCT", "20")
            )

            # ── staleness sustained 2 min ────────────────────────────────
            # Fires only when even the freshest snapshot in the 2-min
            # window is older than 300 s — i.e. the breach has held across
            # the entire window.
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

            # ── divergence sustained 5 min ───────────────────────────────
            div_rate_5m = (
                (window_5m["bad_divergence"] / window_5m["total"] * 100.0)
                if window_5m["total"] else 0.0
            )
            div_rate_1m = (
                (window_1m["bad_divergence"] / window_1m["total"] * 100.0)
                if (window_1m and window_1m["total"]) else 0.0
            )
            sustained_div = (
                div_rate_5m >= divergence_threshold_pct
                and window_5m["total"] >= 5
                and (
                    window_1m is None or window_1m["total"] == 0
                    or div_rate_1m >= divergence_threshold_pct
                )
            )
            if sustained_div and await _rate_limit_check_async(
                "divergence", redis_client
            ):
                msg = (
                    ":warning: *Robust indicators* — `>10%` divergence rate "
                    f"{div_rate_5m:.1f}% sustained over last 5 minutes "
                    f"(threshold {divergence_threshold_pct:.1f}%, samples={window_5m['total']})."
                )
                await _send_ops_alert(msg)
                report["fired"].append("divergence")
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


# ─── Phase 3: legacy-rollback standby check ──────────────────────────────────

# 24h SLA for the emergency rollback. If LEGACY_PIPELINE_ROLLBACK has
# been observed True for longer than this we page ops every check tick
# (rate-limited to one alert per 6 hours so the channel doesn't spam).
_ROLLBACK_STANDBY_SECONDS = 24 * 60 * 60
_ROLLBACK_FIRST_SEEN_KEY = "robust_alerts:legacy_rollback:first_seen"
_ROLLBACK_RATE_LIMIT_SECONDS = 6 * 60 * 60
_LOCAL_ROLLBACK_FIRST_SEEN: dict[str, float] = {}


async def _record_rollback_first_seen(redis_client, now: float) -> Optional[float]:
    """Persist the first-seen timestamp for an active rollback.

    Returns the recorded first-seen timestamp (which may pre-date
    ``now`` if the flag has been on for a while). Falls back to an
    in-process dict when Redis is unreachable so a Redis blip doesn't
    silently mask a stale rollback.
    """
    if redis_client is not None:
        try:
            await redis_client.set(
                _ROLLBACK_FIRST_SEEN_KEY, str(now), nx=True
            )
            stored = await redis_client.get(_ROLLBACK_FIRST_SEEN_KEY)
            if stored is not None:
                return float(stored)
        except Exception as exc:
            logger.debug(
                "[robust_alerts] redis rollback first-seen failed: %s", exc
            )

    if _ROLLBACK_FIRST_SEEN_KEY not in _LOCAL_ROLLBACK_FIRST_SEEN:
        _LOCAL_ROLLBACK_FIRST_SEEN[_ROLLBACK_FIRST_SEEN_KEY] = now
    return _LOCAL_ROLLBACK_FIRST_SEEN[_ROLLBACK_FIRST_SEEN_KEY]


async def _clear_rollback_first_seen(redis_client) -> None:
    """Clear the first-seen timestamp once the rollback is unset."""
    if redis_client is not None:
        try:
            await redis_client.delete(_ROLLBACK_FIRST_SEEN_KEY)
        except Exception as exc:
            logger.debug(
                "[robust_alerts] redis rollback clear failed: %s", exc
            )
    _LOCAL_ROLLBACK_FIRST_SEEN.pop(_ROLLBACK_FIRST_SEEN_KEY, None)


def _standby_check_environment() -> str:
    """Return the deployment environment for the standby check.

    Reads ``ROBUST_ALERTS_ENVIRONMENT`` first (explicit gating),
    falls back to ``APP_ENV`` / ``ENVIRONMENT`` / ``ENV``. Defaults
    to ``"production"`` so existing production deployments keep
    paging without configuration changes.
    """
    for var in ("ROBUST_ALERTS_ENVIRONMENT", "APP_ENV", "ENVIRONMENT", "ENV"):
        val = os.environ.get(var)
        if val and val.strip():
            return val.strip().lower()
    return "production"


def _standby_alerts_enabled() -> bool:
    """Phase 3 gate: only emit standby ops alerts in production.

    Non-production environments (dev / staging / test) frequently
    flip ``LEGACY_PIPELINE_ROLLBACK`` while validating the runbook.
    Without this gate, every >24h test session would page ops. The
    gate can be overridden by setting
    ``ROBUST_ALERTS_FORCE_STANDBY=true`` (e.g. for a staging fire
    drill).
    """
    force = os.environ.get("ROBUST_ALERTS_FORCE_STANDBY", "").strip().lower()
    if force in ("1", "true", "yes", "on"):
        return True
    return _standby_check_environment() in ("production", "prod")


async def _check_legacy_rollback_standby_async() -> dict:
    """Inspect the rollback flag and page ops if it has been on >24h.

    Production-gated: ops alerts only fire when
    ``_standby_alerts_enabled()`` returns True (production env, or
    ``ROBUST_ALERTS_FORCE_STANDBY=true``). The first-seen bookkeeping
    still runs in non-prod so we can introspect the report shape, but
    the slack/webhook page is suppressed with ``skipped="non_production"``.
    """
    from ..services.robust_indicators import is_legacy_rollback_active

    report: dict = {"rollback_active": False, "fired": False}
    rollback_active = False
    try:
        rollback_active = bool(is_legacy_rollback_active())
    except Exception as exc:
        logger.warning("[robust_alerts] rollback flag read failed: %s", exc)
        report["error"] = str(exc)
        return report

    report["rollback_active"] = rollback_active
    report["environment"] = _standby_check_environment()

    try:
        from ..services.config_service import _make_redis_client
        redis_client = _make_redis_client()
    except Exception:
        redis_client = None

    if not rollback_active:
        await _clear_rollback_first_seen(redis_client)
        return report

    now = datetime.now(timezone.utc).timestamp()
    first_seen = await _record_rollback_first_seen(redis_client, now)
    if first_seen is None:
        first_seen = now

    age_seconds = max(0.0, now - first_seen)
    report["age_seconds"] = age_seconds
    report["first_seen"] = first_seen

    if age_seconds < _ROLLBACK_STANDBY_SECONDS:
        return report

    # Production gate: suppress the page in non-prod environments so a
    # staging or local validation of the runbook doesn't wake ops.
    if not _standby_alerts_enabled():
        report["skipped"] = "non_production"
        logger.info(
            "[robust_alerts] standby alert suppressed in env=%s "
            "(set ROBUST_ALERTS_FORCE_STANDBY=true to override)",
            report["environment"],
        )
        return report

    # Use the per-call ttl_seconds parameter so the standby alert's
    # 6-hour rate-limit window is registered cleanly without touching
    # the global ``_RATE_LIMIT_SECONDS`` used by the high-frequency
    # sustained-condition alerts.
    rl_key = "legacy_rollback_standby"
    if await _rate_limit_check_async(
        rl_key, redis_client, ttl_seconds=_ROLLBACK_RATE_LIMIT_SECONDS
    ):
        hours = age_seconds / 3600.0
        msg = (
            ":rotating_light: *Robust indicators* — "
            "`LEGACY_PIPELINE_ROLLBACK` has been ACTIVE for "
            f"{hours:.1f}h (>24h SLA). Every score read is being "
            "served by the legacy engine. Confirm intent and unset "
            "the flag once the incident is resolved."
        )
        await _send_ops_alert(msg)
        report["fired"] = True

    return report


@celery_app.task(name="app.tasks.robust_alerts.check_legacy_rollback_standby")
def check_legacy_rollback_standby() -> dict:
    """Celery task entry point — runs the hourly rollback standby check.

    Scheduled hourly via ``robust_indicator_legacy_rollback_check`` so
    a single failed beat tick still leaves us inside the 24-hour SLA.
    """
    try:
        report = _run_async(_check_legacy_rollback_standby_async())
    except Exception as exc:
        logger.warning(
            "[robust_alerts] rollback standby crashed: %s", exc, exc_info=True
        )
        return {"rollback_active": False, "fired": False, "error": str(exc)}
    if report.get("fired"):
        logger.warning(
            "[robust_alerts] legacy rollback standby alert fired: %s", report
        )
    return report
