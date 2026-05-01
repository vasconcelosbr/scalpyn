"""Admin API for the robust-indicators rollout (Phase 3 — deprecation).

Routes (all admin-only):

    GET  /api/admin/robust-indicators/status
        Returns the formal phase tag (``"deprecation"``), the current
        rollout percent, the symbol bucketing counts (mostly useful as
        a diagnostic now that the robust engine is the default
        everywhere), the silent-fallback tally, the 7-day rolling
        stability window, and the rollback flag. When
        ``LEGACY_PIPELINE_ROLLBACK`` is true the response prominently
        flags it.

    POST /api/admin/robust-indicators/preflight
        Body: ``{"target_percent": 50}``. Runs the legacy pre-flight
        guard without mutating any state — useful as a forward-looking
        sanity check before raising the rollout percent. Phase 3 keeps
        the endpoint for ops continuity even though the rollout is
        already at 100%.

The endpoints are read-only — neither the rollout percent nor the
rollback flag are mutated through HTTP. Both are owned by env vars
(``USE_ROBUST_INDICATORS_PERCENT`` / ``LEGACY_PIPELINE_ROLLBACK``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.user import User
from ..api.config import get_current_user_id
from ..config import settings
from ..services.robust_indicators import (
    bucketed_symbols,
    check_safe_to_raise,
    collect_window_metrics,
    get_rollout_percent,
    is_legacy_rollback_active,
)
from ..services.robust_indicators.metrics import silent_fallback_snapshot
from ..services.robust_indicators.preflight import (
    DIVERGENCE_RATE_MAX,
    MIN_GLOBAL_CONFIDENCE,
    REJECTION_RATE_MAX,
    WINDOW_MINUTES,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/admin/robust-indicators", tags=["Admin"])


# Phase 3 alert thresholds — if any 7-day rolling metric drifts back
# into the Phase 2 pre-flight unsafe zone we surface an alert in the
# admin response so on-call notices before the hourly standby task does.
_PHASE = "deprecation"
_OBSERVATION_WINDOW_DAYS = 7


_NEXT_TIER = {0: 10, 10: 50, 50: 100, 100: 100}


def _next_tier(current_percent: int) -> int:
    """Return the next rollout tier (10 → 50 → 100, capped at 100)."""
    return _NEXT_TIER.get(int(current_percent), min(100, max(0, int(current_percent) + 10)))


async def _require_admin(
    db: AsyncSession,
    user_id,
) -> User:
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    role = (getattr(user, "role", None) or "trader").lower()
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


class PreflightRequest(BaseModel):
    target_percent: int


async def _bucketing_summary(db: AsyncSession, percent: int) -> Dict[str, Any]:
    """Aggregate bucketed/legacy/fallback counts from the live asset table.

    The query joins on the live ``pipeline_watchlist_assets`` table so
    the answer reflects what users are actually seeing right now. We
    re-derive the bucket membership in Python (instead of reading
    ``engine_tag``) because ``engine_tag`` reflects the bucketing in
    force at the LAST scan — which can lag the env var by up to one
    scan interval right after a tier bump.
    """
    try:
        rows = (await db.execute(
            text(
                """
                SELECT symbol, engine_tag
                FROM pipeline_watchlist_assets
                WHERE level_direction IS NULL OR level_direction = 'up'
                """
            )
        )).fetchall()
    except Exception as exc:
        logger.warning("[admin_robust] bucketing summary failed: %s", exc)
        return {
            "active_symbols": 0,
            "bucketed_symbols": 0,
            "legacy_symbols": 0,
            "engine_tag_robust": 0,
            "engine_tag_legacy": 0,
            "engine_tag_unknown": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    active_syms = {(r.symbol or "").upper() for r in rows if r.symbol}
    by_tag: Dict[str, int] = {"robust": 0, "legacy": 0, "unknown": 0}
    for r in rows:
        tag = (r.engine_tag or "unknown").lower()
        by_tag[tag] = by_tag.get(tag, 0) + 1

    bucketed = bucketed_symbols(active_syms, percent=percent)
    return {
        "active_symbols": len(active_syms),
        "bucketed_symbols": len(bucketed),
        "legacy_symbols": len(active_syms) - len(bucketed),
        "engine_tag_robust": by_tag.get("robust", 0),
        "engine_tag_legacy": by_tag.get("legacy", 0),
        "engine_tag_unknown": by_tag.get("unknown", 0),
    }


async def _seven_day_trend(db: AsyncSession) -> Dict[str, Any]:
    """Return per-day rejection / divergence / confidence trends over 7 days.

    Used by the admin status endpoint as the Phase 3 stability window.
    Each day bucket carries the count of snapshots, the rejection rate,
    the share of snapshots that landed in the ``>10%`` divergence
    bucket, and the average confidence. When any single day drifts
    back into the Phase 2 pre-flight unsafe zone the day is tagged
    ``unsafe=True`` and the rolling summary's ``alert`` flag flips.
    """
    try:
        rows = (await db.execute(text(
            """
            SELECT
                date_trunc('day', timestamp)                                  AS day,
                COUNT(*)                                                      AS total,
                SUM(CASE WHEN rejection_reason IS NOT NULL THEN 1 ELSE 0 END) AS rejected,
                SUM(CASE WHEN divergence_bucket = '>10%' THEN 1 ELSE 0 END)   AS divergent_high,
                AVG(global_confidence)                                        AS avg_confidence
            FROM indicator_snapshots
            WHERE timestamp > now() - interval '7 days'
            GROUP BY 1
            ORDER BY 1 DESC
            """
        ))).fetchall()
    except Exception as exc:
        logger.warning("[admin_robust] 7-day trend query failed: %s", exc)
        return {
            "window_days": _OBSERVATION_WINDOW_DAYS,
            "days": [],
            "summary": {
                "total": 0,
                "rejected": 0,
                "divergent_high": 0,
                "avg_confidence": 0.0,
                "rejection_rate": 0.0,
                "divergence_rate": 0.0,
                "alert": False,
                "alert_reasons": [],
            },
            "error": f"{type(exc).__name__}: {exc}",
        }

    days: list[Dict[str, Any]] = []
    grand_total = 0
    grand_rejected = 0
    grand_divergent_high = 0
    weighted_conf = 0.0

    for r in rows:
        total = int(r.total or 0)
        rejected = int(r.rejected or 0)
        divergent = int(r.divergent_high or 0)
        avg_conf = float(r.avg_confidence) if r.avg_confidence is not None else 0.0
        rej_rate = (rejected / total) if total else 0.0
        div_rate = (divergent / total) if total else 0.0
        unsafe_reasons: list[str] = []
        if total > 0:
            if rej_rate > REJECTION_RATE_MAX:
                unsafe_reasons.append(
                    f"rejection_rate {rej_rate:.3f} > {REJECTION_RATE_MAX:.3f}"
                )
            if div_rate > DIVERGENCE_RATE_MAX:
                unsafe_reasons.append(
                    f"divergence_rate {div_rate:.3f} > {DIVERGENCE_RATE_MAX:.3f}"
                )
            if avg_conf < MIN_GLOBAL_CONFIDENCE:
                unsafe_reasons.append(
                    f"avg_confidence {avg_conf:.3f} < {MIN_GLOBAL_CONFIDENCE:.3f}"
                )
        days.append({
            "day": r.day.isoformat() if r.day is not None else None,
            "total": total,
            "rejected": rejected,
            "divergent_high": divergent,
            "avg_confidence": round(avg_conf, 4),
            "rejection_rate": round(rej_rate, 4),
            "divergence_rate": round(div_rate, 4),
            "unsafe": bool(unsafe_reasons),
            "unsafe_reasons": unsafe_reasons,
        })
        grand_total += total
        grand_rejected += rejected
        grand_divergent_high += divergent
        weighted_conf += avg_conf * total

    summary_avg_conf = (weighted_conf / grand_total) if grand_total else 0.0
    summary_rej_rate = (grand_rejected / grand_total) if grand_total else 0.0
    summary_div_rate = (grand_divergent_high / grand_total) if grand_total else 0.0
    summary_alerts: list[str] = []
    if grand_total > 0:
        if summary_rej_rate > REJECTION_RATE_MAX:
            summary_alerts.append(
                f"rejection_rate {summary_rej_rate:.3f} > {REJECTION_RATE_MAX:.3f}"
            )
        if summary_div_rate > DIVERGENCE_RATE_MAX:
            summary_alerts.append(
                f"divergence_rate {summary_div_rate:.3f} > {DIVERGENCE_RATE_MAX:.3f}"
            )
        if summary_avg_conf < MIN_GLOBAL_CONFIDENCE:
            summary_alerts.append(
                f"avg_confidence {summary_avg_conf:.3f} < {MIN_GLOBAL_CONFIDENCE:.3f}"
            )

    # Phase 3 fail-loud contract: ANY single day in the window that
    # drifts back into the pre-flight unsafe zone must surface a
    # top-level alert — even if the 7-day aggregate stays within
    # bounds. Day-level drift is the canary; aggregating it away would
    # let a single bad day hide.
    unsafe_days = [
        d for d in days if d.get("unsafe") and d.get("total", 0) > 0
    ]
    for d in unsafe_days:
        for reason in d.get("unsafe_reasons", []):
            summary_alerts.append(f"{d['day']}: {reason}")

    return {
        "window_days": _OBSERVATION_WINDOW_DAYS,
        "days": days,
        "summary": {
            "total": grand_total,
            "rejected": grand_rejected,
            "divergent_high": grand_divergent_high,
            "avg_confidence": round(summary_avg_conf, 4),
            "rejection_rate": round(summary_rej_rate, 4),
            "divergence_rate": round(summary_div_rate, 4),
            "unsafe_day_count": len(unsafe_days),
            "alert": bool(summary_alerts),
            "alert_reasons": summary_alerts,
        },
    }


@router.get("/status")
async def get_status(
    target_percent: Optional[int] = Query(
        None,
        ge=0, le=100,
        description=(
            "Optional target percent for the pre-flight guard. Defaults "
            "to the next tier (10 → 50 → 100). Phase 3: rollout is "
            "already at 100% so this is largely informational."
        ),
    ),
    user_id=Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Return the rollout status payload (admin-only, Phase 3)."""
    await _require_admin(db, user_id)

    current = get_rollout_percent()
    tier = int(target_percent) if target_percent is not None else _next_tier(current)

    bucketing = await _bucketing_summary(db, current)
    window_metrics = await collect_window_metrics(db)
    preflight = await check_safe_to_raise(db, target_percent=tier)
    fallback = silent_fallback_snapshot()
    rollback_active = is_legacy_rollback_active()
    seven_day = await _seven_day_trend(db)

    response: Dict[str, Any] = {
        "phase": _PHASE,
        "rollout": {
            "current_percent": current,
            "next_tier": _next_tier(current),
            "force_rollout_raise": bool(getattr(settings, "FORCE_ROLLOUT_RAISE", False)),
        },
        "rollback_active": rollback_active,
        "bucketing": bucketing,
        "shadow_window": window_metrics,
        "seven_day_trend": seven_day,
        "thresholds": {
            "divergence_rate_max": DIVERGENCE_RATE_MAX,
            "rejection_rate_max": REJECTION_RATE_MAX,
            "min_global_confidence": MIN_GLOBAL_CONFIDENCE,
            "window_minutes": WINDOW_MINUTES,
            "observation_window_days": _OBSERVATION_WINDOW_DAYS,
        },
        "preflight": preflight.to_dict(),
        "silent_fallbacks": {
            "by_reason": fallback,
            "total": sum(fallback.values()),
        },
    }

    # Surface the rollback prominently — the admin UI keys off this top-
    # level block to render an alert banner instead of having to crawl
    # the nested ``rollback_active`` field.
    if rollback_active:
        response["alert"] = {
            "kind": "legacy_rollback_active",
            "severity": "critical",
            "message": (
                "LEGACY_PIPELINE_ROLLBACK is active — every score read "
                "is being served by the legacy engine. Confirm this is "
                "intentional and unset the flag once the incident is "
                "resolved."
            ),
        }
    elif seven_day.get("summary", {}).get("alert"):
        response["alert"] = {
            "kind": "seven_day_drift",
            "severity": "warning",
            "message": (
                "7-day rolling stability window drifted back into the "
                "Phase 2 pre-flight unsafe zone. Reasons: "
                + "; ".join(seven_day["summary"].get("alert_reasons", []))
            ),
        }

    return response


@router.post("/preflight")
async def post_preflight(
    body: PreflightRequest,
    user_id=Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Run the pre-flight guard for an explicit ``target_percent``."""
    await _require_admin(db, user_id)
    if body.target_percent < 0 or body.target_percent > 100:
        raise HTTPException(status_code=400, detail="target_percent must be 0..100")
    preflight = await check_safe_to_raise(db, target_percent=body.target_percent)
    return preflight.to_dict()
