"""Macro API — exposes the current macro regime from the Futures Macro Gate.

Routes:
  GET /api/macro/regime  → current macro regime (from active futures engine)
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..api.config import get_current_user_id
from ..engines.futures_scanner import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/macro", tags=["Macro"])


@router.get("/regime")
async def get_macro_regime(
    user_id: UUID = Depends(get_current_user_id),
):
    """Return the latest macro regime evaluation from the active futures engine.

    Requires the futures engine to be running; returns 503 if it is not.
    """
    scanner = get_engine(str(user_id))
    if not scanner:
        raise HTTPException(
            status_code=503,
            detail="Futures engine is not running. Start it to obtain macro regime data.",
        )

    try:
        state = await scanner._macro.get_regime()
    except Exception as exc:
        logger.exception("Failed to evaluate macro regime for user %s: %s", user_id, exc)
        raise HTTPException(status_code=500, detail=f"Macro evaluation failed: {exc}")

    return {
        "regime":           state.regime,
        "score":            round(state.score, 2),
        "component_scores": {k: round(v, 2) for k, v in state.component_scores.items()},
        "size_modifier":    round(state.size_modifier, 3),
        "allows_long":      state.allows_long,
        "allows_short":     state.allows_short,
        "evaluated_at":     state.timestamp,
        "details":          state.details,
    }
