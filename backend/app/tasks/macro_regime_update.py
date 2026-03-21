"""
Celery Task — macro regime update.
Runs every 30 minutes.

Evaluates current macro conditions using ``FuturesMacroGate`` and stores the
result in Redis.  If the regime changed since the last evaluation, the new
state is broadcast to all WebSocket clients subscribed to the "macro" channel.

Flow:
  1. Connect to Redis and read the previous regime from ``macro:regime``.
  2. Load a ``GateAdapter`` using the first active exchange connection in the DB
     (system-level call — not per-user).
  3. Instantiate ``FuturesMacroGate`` with a system-default ``FuturesEngineConfig``.
  4. Call ``macro_gate.get_regime(force_refresh=True)`` to get a fresh ``MacroState``.
  5. Serialise the result and write it to Redis as ``macro:regime`` (TTL 3 600 s).
  6. Broadcast via ``broadcast_macro_update`` if the regime label changed.
  7. Return a summary dict.

Registered as: ``app.tasks.macro_regime_update.update``
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_REDIS_KEY = "macro:regime"
_REDIS_TTL = 3600  # seconds — 1 hour


# ── Async runner ──────────────────────────────────────────────────────────────

def _run_async(coro) -> Any:
    """Execute an async coroutine in a fresh event loop (Celery worker context)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Core async logic ──────────────────────────────────────────────────────────

async def _update_async() -> dict[str, Any]:
    import redis.asyncio as aioredis
    from sqlalchemy import select

    from ..config import settings
    from ..database import AsyncSessionLocal
    from ..engines.futures_macro_gate import FuturesMacroGate
    from ..exchange_adapters.gate_adapter import GateAdapter
    from ..models.exchange_connection import ExchangeConnection
    from ..schemas.futures_engine_config import FuturesEngineConfig
    from ..utils.encryption import decrypt
    from ..websocket.scalpyn_ws_server import broadcast_macro_update

    logger.info("Macro regime update: starting evaluation")

    # ── 1. Connect to Redis ───────────────────────────────────────────────────
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)

    try:
        previous_regime: str | None = None
        try:
            raw = await redis_client.get(_REDIS_KEY)
            if raw:
                prev_data = json.loads(raw)
                previous_regime = prev_data.get("regime")
        except Exception as exc:
            logger.warning("Macro regime update: could not read previous regime from Redis: %s", exc)

        # ── 2. Load the first active exchange connection in the DB ────────────
        adapter: GateAdapter | None = None
        async with AsyncSessionLocal() as db:
            conn_result = await db.execute(
                select(ExchangeConnection).where(
                    ExchangeConnection.is_active == True,  # noqa: E712
                ).order_by(ExchangeConnection.execution_priority).limit(1)
            )
            conn = conn_result.scalars().first()

        if conn is None:
            logger.warning(
                "Macro regime update: no active exchange connection found — "
                "macro evaluation will use API calls that may fail gracefully"
            )
            # Still attempt evaluation; individual component scorers catch errors.
            adapter = GateAdapter("", "")
        else:
            try:
                raw_key = conn.api_key_encrypted
                raw_secret = conn.api_secret_encrypted
                adapter = GateAdapter(
                    decrypt(raw_key).strip(),
                    decrypt(raw_secret).strip(),
                )
            except Exception as exc:
                logger.error(
                    "Macro regime update: failed to build GateAdapter: %s — "
                    "proceeding with empty credentials",
                    exc,
                )
                adapter = GateAdapter("", "")

        # ── 3. Build FuturesMacroGate with system-default config ──────────────
        cfg = FuturesEngineConfig()
        macro_gate = FuturesMacroGate(cfg.macro, adapter)

        # ── 4. Evaluate macro regime ──────────────────────────────────────────
        state = await macro_gate.get_regime(force_refresh=True)

        regime: str = state.regime
        score: float = state.score
        components: dict[str, Any] = state.component_scores

        # ── 5. Store in Redis ─────────────────────────────────────────────────
        payload = {
            "regime": regime,
            "score": score,
            "components": components,
            "allows_long": state.allows_long,
            "allows_short": state.allows_short,
            "size_modifier": state.size_modifier,
            "timestamp": state.timestamp,
            "details": state.details,
        }
        try:
            await redis_client.set(_REDIS_KEY, json.dumps(payload), ex=_REDIS_TTL)
            logger.info(
                "Macro regime update: cached to Redis — regime=%s score=%.1f",
                regime,
                score,
            )
        except Exception as exc:
            logger.error("Macro regime update: failed to write to Redis: %s", exc)

        # ── 6. Broadcast if regime changed ────────────────────────────────────
        changed: bool = regime != previous_regime
        if changed:
            logger.info(
                "Macro regime CHANGED: %s → %s (score=%.1f)",
                previous_regime,
                regime,
                score,
            )
            try:
                await broadcast_macro_update(regime, score, components)
            except Exception as exc:
                logger.warning(
                    "Macro regime update: broadcast_macro_update failed: %s", exc
                )
        else:
            logger.info(
                "Macro regime unchanged: %s (score=%.1f)", regime, score
            )

        return {
            "regime": regime,
            "score": score,
            "previous_regime": previous_regime,
            "changed": changed,
            "components": components,
        }

    finally:
        await redis_client.aclose()


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.macro_regime_update.update", bind=True, max_retries=0)
def update(self) -> str:
    """
    Celery periodic task — macro regime update.
    Scheduled every 30 minutes via beat_schedule in celery_app.py.
    """
    try:
        result = _run_async(_update_async())
        changed_flag = "CHANGED" if result.get("changed") else "unchanged"
        return (
            f"Macro regime: {result.get('regime')} "
            f"(score={result.get('score')}, {changed_flag})"
        )
    except Exception as exc:
        logger.exception("Macro regime update task failed: %s", exc)
        return f"Macro regime update FAILED: {exc}"
