"""Admin API for the robust-indicators Phase 2 rollout.

Routes (all admin-only):

    GET  /api/admin/robust-indicators/status
        Returns the current rollout percent, the symbol bucketing
        counts (bucketed vs legacy) computed against the active
        watchlist asset table, the silent-fallback tally, and the
        pre-flight safety guard outcome for the *next* tier (or for
        the explicit ``target_percent`` query param).

    POST /api/admin/robust-indicators/preflight
        Body: ``{"target_percent": 50}``. Runs the pre-flight guard
        without mutating any state — useful from CI before bumping the
        env var. Honours ``FORCE_ROLLOUT_RAISE`` exactly the same way
        as the runtime path.

The endpoints are read-only (the rollout percent itself is owned by
``USE_ROBUST_INDICATORS_PERCENT`` in env, NOT mutated through HTTP).
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


@router.get("/status")
async def get_status(
    target_percent: Optional[int] = Query(
        None,
        ge=0, le=100,
        description=(
            "Optional target percent for the pre-flight guard. Defaults "
            "to the next tier (10 → 50 → 100)."
        ),
    ),
    user_id=Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, Any]:
    """Return the rollout status payload (admin-only)."""
    await _require_admin(db, user_id)

    current = get_rollout_percent()
    tier = int(target_percent) if target_percent is not None else _next_tier(current)

    bucketing = await _bucketing_summary(db, current)
    window_metrics = await collect_window_metrics(db)
    preflight = await check_safe_to_raise(db, target_percent=tier)
    fallback = silent_fallback_snapshot()

    return {
        "rollout": {
            "current_percent": current,
            "next_tier": _next_tier(current),
            "force_rollout_raise": bool(getattr(settings, "FORCE_ROLLOUT_RAISE", False)),
        },
        "bucketing": bucketing,
        "shadow_window": window_metrics,
        "thresholds": {
            "divergence_rate_max": DIVERGENCE_RATE_MAX,
            "rejection_rate_max": REJECTION_RATE_MAX,
            "min_global_confidence": MIN_GLOBAL_CONFIDENCE,
            "window_minutes": WINDOW_MINUTES,
        },
        "preflight": preflight.to_dict(),
        "silent_fallbacks": {
            "by_reason": fallback,
            "total": sum(fallback.values()),
        },
    }


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
