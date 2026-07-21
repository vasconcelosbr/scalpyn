"""Celery Task — Pipeline Scan (L1 → L2 → L3).

Runs every 5 minutes (triggered by compute_5m chain or beat schedule).
For each active user:
  1. Fetch all PipelineWatchlists (POOL / L1 / L2 / L3)
  2. Resolve the symbol universe per watchlist (from Pool or parent watchlist)
  3. Fetch market data (indicators + alpha_scores + market_metadata)
  4. Apply ProfileEngine filters/scoring per level
  5. Persist results in pipeline_watchlist_assets (upsert)
  6. Compare with prior snapshot in Redis → detect new L3 signals
  7. Broadcast new signals via WebSocket (channel "signals" + "pipeline")
"""

import asyncio
import json
import logging
import os
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Optional
from uuid import UUID, uuid4

from ..tasks.celery_app import celery_app
from ..services.pipeline_rejections import evaluate_rejections
from ..utils.pipeline_profile_filters import (
    STRICT_META_FIELDS,
    effective_pipeline_level,
    resolve_pipeline_dependency,
    select_profile_filter_conditions,
    uses_pipeline_filters,
    WATCHLIST_STAGE_ORDER,
)

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "spe:pipeline:"   # Redis key prefix per watchlist
_LAST_SUCCESS_KEY = "scalpyn:pipeline_scan:last_success_at"
_SAFETY_STALE_SECONDS = int(
    os.environ.get("PIPELINE_SCAN_SAFETY_STALE_SECONDS", "420")
)
_SCAN_COALESCE_SECONDS = int(
    os.environ.get("PIPELINE_SCAN_COALESCE_SECONDS", "240")
)
_SCAN_MESSAGE_EXPIRES_SECONDS = int(
    os.environ.get("PIPELINE_SCAN_MESSAGE_EXPIRES_SECONDS", "600")
)

# Default staleness threshold (minutes).  Assets not re-confirmed within this
# window are automatically marked 'down'.  Override per-watchlist via
# filters_json.staleness_minutes (GUI-editable).
# Override globally via env var PIPELINE_SCAN_STALENESS_MINUTES (default 60).
# 60 min gives the sequential compute_30m loop (up to ~25 min) + scan delay
# enough headroom before valid symbols are evicted from L3.
_DEFAULT_STALENESS_MINUTES: int = int(
    os.environ.get("PIPELINE_SCAN_STALENESS_MINUTES", "60")
)


def _ml_gate_should_block(ml_result: dict | None) -> bool:
    """P2 (Fase 1.6): o ML gate só rebaixa um L3 ALLOW → BLOCK quando um modelo
    ACTIVE emitiu um veredito real de rejeição.

    - modelo aprovou               → não bloqueia (ALLOW mantido).
    - sem modelo elegível (SKIPPED)→ NÃO bloqueia o sinal real (alinha com o
      contrato do prediction_service: "SKIPPED não bloqueia por si só").
    - modelo rejeitou (OK, not approved) ou falha de infra com modelo presente
      (ML_EXCEPTION_FAIL_CLOSED) → bloqueia (fail-closed preservado).
    """
    ml_result = ml_result or {}
    if bool(ml_result.get("model_approved", False)):
        return False
    return ml_result.get("score_status") != "SKIPPED"


def _ml_gate_audit_payload(
    ml_result: dict | None,
    *,
    decision_before_ml: str = "ALLOW",
    decision_after_ml: str,
    model_lane: str = "L3_PROFILE",
) -> dict:
    ml_result = ml_result or {}
    reason_code = ml_result.get("reason_code")
    model_approved = bool(ml_result.get("model_approved", False))
    if not model_approved and not reason_code:
        reason_code = "ML_EXCEPTION_FAIL_CLOSED"
    if not model_approved and reason_code == "NO_ELIGIBLE_MODEL_FOR_LANE":
        fallback_policy = "DISABLED_FOR_L3_WHEN_GATE_ENABLED"
    elif not model_approved:
        fallback_policy = "DISABLED_FOR_L3_WHEN_GATE_ENABLED"
    else:
        fallback_policy = "NOT_USED"
    # gate_action reflete a AÇÃO EFETIVA do gate sobre a decisão (decision_after_ml),
    # não o veredito cru do modelo. P2 fix (Fase 1.6): sem modelo (SKIPPED) o gate
    # não rebaixa o ALLOW, então gate_action=ALLOW mesmo com model_approved=False.
    gate_action = decision_after_ml
    score_status = ml_result.get("score_status") or ("OK" if model_approved else "SKIPPED")
    reason_codes = []
    if reason_code:
        reason_codes.append(reason_code)
    if score_status == "ML_EXCEPTION_FAIL_CLOSED":
        reason_codes.append("ML_PROBABILITY_INVALID")
    if decision_after_ml == "BLOCK":
        reason_codes.append("ML_GATE_BLOCKED")
    else:
        reason_codes.append("ML_GATE_ALLOWED")
    return {
        "ml_gate": gate_action,
        "gate_action": gate_action,
        "model_approved": model_approved,
        "reason_code": reason_code,
        "reason_codes": reason_codes,
        "score_status": score_status,
        "model_lane": ml_result.get("model_lane") or model_lane,
        "model_id": ml_result.get("model_id"),
        "model_version": ml_result.get("model_version"),
        "probability": ml_result.get("win_fast_probability"),
        "threshold_used": ml_result.get("threshold_used"),
        "p_l1_win": ml_result.get("p_l1_win"),
        "l1_model_id": ml_result.get("l1_model_id"),
        "l1_model_version": ml_result.get("l1_model_version"),
        "l1_rank_position": ml_result.get("l1_rank_position"),
        "l1_rank_percentile": ml_result.get("l1_rank_percentile"),
        "l1_ranker_mode": ml_result.get("l1_ranker_mode"),
        "selected_by_l1_ranker": ml_result.get("selected_by_l1_ranker"),
        "decision_before_ml": decision_before_ml,
        "decision_after_ml": decision_after_ml,
        "fallback_used": False,
        "fallback_policy": fallback_policy,
    }


def _l1_ranker_config() -> dict:
    mode = os.environ.get("L1_RANKER_MODE", "top_k").strip().lower()
    if mode not in {"top_k", "percentile"}:
        mode = "top_k"
    try:
        top_k = int(os.environ.get("L1_TOP_K_DEFAULT", "10"))
    except ValueError:
        top_k = 10
    try:
        percentile_min = float(os.environ.get("L1_PERCENTILE_MIN", "90"))
    except ValueError:
        percentile_min = 90.0
    allow_threshold_gate = os.environ.get("L1_ALLOW_THRESHOLD_GATE", "false").strip().lower() == "true"
    return {
        "mode": mode,
        "top_k": max(1, top_k),
        "percentile_min": max(0.0, min(100.0, percentile_min)),
        "allow_threshold_gate": allow_threshold_gate,
    }


def _rank_l1_candidates(items: list[tuple[dict, dict]]) -> dict:
    cfg = _l1_ranker_config()
    ranked: dict = {}
    valid: list[tuple[dict, dict, float]] = []
    for decision, pred in items:
        symbol = decision.get("symbol")
        probability = pred.get("win_fast_probability")
        if pred.get("score_status") != "OK" or probability is None:
            ranked[symbol] = {
                "selected": False,
                "reason_code": pred.get("reason_code") or "L1_MODEL_UNAVAILABLE",
                "reason_codes": [pred.get("reason_code") or "L1_MODEL_UNAVAILABLE"],
                "p_l1_win": probability,
                "l1_model_id": pred.get("model_id"),
                "l1_model_version": pred.get("model_version"),
                "threshold_l1": pred.get("threshold_used"),
                "l1_ranker_mode": cfg["mode"],
                "l1_rank_position": None,
                "l1_rank_percentile": None,
                "selected_by_l1_ranker": False,
            }
            continue
        valid.append((decision, pred, float(probability)))

    valid.sort(key=lambda row: row[2], reverse=True)
    total = len(valid)
    for index, (decision, pred, probability) in enumerate(valid, start=1):
        symbol = decision.get("symbol")
        percentile = round(100.0 * (total - index + 1) / max(total, 1), 4)
        if cfg["mode"] == "percentile":
            selected = percentile >= cfg["percentile_min"]
            reason_code = "L1_PERCENTILE_SELECTED" if selected else "L1_PERCENTILE_REJECTED"
        else:
            selected = index <= cfg["top_k"]
            reason_code = "L1_TOP_K_SELECTED" if selected else "L1_TOP_K_REJECTED"
        ranked[symbol] = {
            "selected": selected,
            "reason_code": reason_code,
            "reason_codes": [reason_code],
            "p_l1_win": probability,
            "l1_model_id": pred.get("model_id"),
            "l1_model_version": pred.get("model_version"),
            "threshold_l1": pred.get("threshold_used"),
            "l1_ranker_mode": cfg["mode"],
            "l1_rank_position": index,
            "l1_rank_percentile": percentile,
            "selected_by_l1_ranker": selected,
        }
    return ranked


def _uuid_or_none(value):
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None

# ── L3 Live Order Flow injection ─────────────────────────────────────────────
# Antes da regra de entrada L3 ser avaliada, sobrescrevemos os indicadores de
# fluxo (``taker_*``, ``buy_pressure``, ``volume_delta``) com o snapshot ao
# vivo do buffer Redis ``trades_buffer:{market_type}:{symbol}`` populado pelo
# WS handler. Os valores em ``indicators`` (DB) podem estar até 5 min velhos
# (frequência do ``compute_5m``) — para uma decisão de execução o frescor
# importa.
#
# Mapeamento { chave em ``asset["indicators"]`` : chave no retorno do
# ``order_flow_service.get_order_flow_data`` }. Use o mesmo nome em ambos os
# lados quando o contrato bate (caso atual).
_LIVE_ORDER_FLOW_FIELDS = {
    "taker_ratio":       "taker_ratio",
    "buy_pressure":      "buy_pressure",
    "taker_buy_volume":  "taker_buy_volume",
    "taker_sell_volume": "taker_sell_volume",
    "volume_delta":      "volume_delta",
}

# Fields computed during pipeline scoring/profile evaluation that must be
# persisted with the decision-time snapshot. They are not raw indicators from
# indicators_history, but they are rules/features that L3 profiles actually
# evaluate and the ML dataset needs to learn from.
_DECISION_CONTEXT_SNAPSHOT_FIELDS = (
    "score",
    "liquidity_score",
    "market_structure_score",
    "momentum_score",
    "signal_score",
    "atr_percent",
    "spread_pct",
    "di_trend",
)
# Janela do agregado live em segundos. Tempo curto → mais sensível ao
# regime corrente; tempo longo → mais robusto a ruído. Configurável via
# ``ConfigProfile(config_type="pipeline")["l3_order_flow_window_seconds"]``.
_ORDER_FLOW_WINDOW_CONFIG_KEY = "l3_order_flow_window_seconds"
_ORDER_FLOW_WINDOW_DEFAULT = 60
# Idade máxima permitida (segundos) para o trade mais recente do snapshot
# live. Acima disso, a decisão é BLOQUEADA (skipped no ciclo, re-tentada
# no próximo). Defesa explícita contra "decidir com dado morto" quando
# o WS leader está com lag ou caiu. Configurável via
# ``ConfigProfile(config_type="pipeline")["l3_order_flow_max_age_seconds"]``.
_ORDER_FLOW_MAX_AGE_CONFIG_KEY = "l3_order_flow_max_age_seconds"
_ORDER_FLOW_MAX_AGE_DEFAULT = 15

# Strict metadata fields — NULL means FAIL (not skip) in profile filters.
# Used by diagnostic logging in _apply_level_filter.
_DIAG_STRICT_META = STRICT_META_FIELDS


def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Creates a dedicated event loop per task invocation. Drains all pending
    asyncpg tasks and disposes the NullPool engine before closing the loop.

    Without dispose + drain, asyncpg schedules _terminate_graceful_close
    via loop.create_task() during GC of NullPool connections after loop.close(),
    causing RuntimeError: Event loop is closed on the next invocation.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        # Step 1 — cancel and drain pending asyncio tasks.
        try:
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
        except BaseException as exc:
            logger.debug("[_run_async] pending-task drain failed: %s", exc)

        # Step 2 — graceful engine dispose (closes asyncpg sockets in-loop).
        try:
            from ..database import _celery_engine
            loop.run_until_complete(_celery_engine.dispose())
            # Step 2b (Task #300 review) — drain microtasks scheduled
            # during dispose() (asyncpg finalizers) before hard-terminate
            # so half-released sockets don't re-arm GC callbacks on a
            # loop we're about to close.
            loop.run_until_complete(asyncio.sleep(0))
        except BaseException as exc:
            logger.debug("[_run_async] _celery_engine.dispose failed: %s", exc)

        # Step 3 — hard-terminate any asyncpg connection still cached on the pool.
        try:
            from ..database import _celery_engine as _ce
            sync_pool = _ce.sync_engine.pool
            records = list(getattr(sync_pool, "_all_conns", None) or [])
            for record in records:
                raw = (
                    getattr(record, "dbapi_connection", None)
                    or getattr(record, "connection", None)
                )
                asyncpg_conn = (
                    getattr(raw, "_connection", None)
                    or getattr(raw, "connection", None)
                    or raw
                )
                terminate = getattr(asyncpg_conn, "terminate", None)
                if callable(terminate):
                    try:
                        terminate()
                    except BaseException:
                        pass
        except BaseException as exc:
            logger.debug("[_run_async] hard-terminate sweep failed: %s", exc)

        # Step 4 — drain async generators registered on the loop.
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except BaseException as exc:
            logger.debug("[_run_async] shutdown_asyncgens failed: %s", exc)

        # Step 5 — close the loop. Always last; never propagate.
        try:
            loop.close()
        except BaseException as exc:
            logger.debug("[_run_async] loop.close failed: %s", exc)
        try:
            asyncio.set_event_loop(None)
        except BaseException:
            pass


# ─── helpers ─────────────────────────────────────────────────────────────────

_PIPELINE_EXECUTION_ORDER = ("POOL", "L1", "L2", "L3")

def _uses_pipeline_filters(level: Optional[str]) -> bool:
    """Only POOL/L1/L2/L3 are filter-enforced pipeline stages."""
    return uses_pipeline_filters(level)


def _log_pipeline_event(
    *,
    level: str,
    execution_id: str,
    event_type: str,
    watchlist_id: Optional[str] = None,
    symbol: Optional[str] = None,
    **extra: Any,
) -> None:
    payload: dict[str, Any] = {
        "type": event_type,
        "level": level,
        "execution_id": execution_id,
    }
    if watchlist_id:
        payload["watchlist_id"] = watchlist_id
    if symbol:
        payload["symbol"] = symbol
    payload.update(extra)
    logger.error(payload)


def _log_stage_processing_summary(
    *,
    level: str,
    input_count: int,
    approved_count: int,
    rejected_count: int,
    watchlist_id: str,
    execution_id: str,
) -> None:
    if level == "POOL":
        logger.info("[POOL] scanned: %d assets", input_count)
        logger.info("[POOL] approved: %d", approved_count)
        return

    if level == "L1":
        total_processed = approved_count + rejected_count
        logger.info("[L1] input received: %d assets", input_count)
        logger.info("[L1] approved: %d", approved_count)
        logger.info("[L1] rejected: %d", rejected_count)
        logger.info("[L1] total processed: %d", total_processed)
        if total_processed != input_count:
            message = (
                f"Asset count mismatch: processed {total_processed} "
                f"but received {input_count}"
            )
            _log_pipeline_event(
                level=level,
                execution_id=execution_id,
                event_type="PIPELINE_INCONSISTENCY",
                watchlist_id=watchlist_id,
                expected=input_count,
                actual=total_processed,
                reason="processed_count_mismatch",
                message=message,
            )


def _normalize_sources_for_scan(
    *,
    level: str,
    watchlist_id: str,
    source_pool_id: Optional[str],
    source_watchlist_id: Optional[str],
    execution_id: str,
) -> tuple[Optional[str], Optional[str]]:
    normalized_level = (level or "").upper()
    pool_id = str(source_pool_id) if source_pool_id else None
    watchlist_source_id = str(source_watchlist_id) if source_watchlist_id else None

    if normalized_level in {"L1", "L2", "L3"} and pool_id and watchlist_source_id:
        logger.warning(
            {
                "type": "INVALID_SOURCE_CONFIG",
                "watchlist_id": watchlist_id,
                "level": normalized_level,
                "execution_id": execution_id,
                "message": "Both source_pool_id and source_watchlist_id set; prioritizing source_watchlist_id.",
            }
        )
        pool_id = None
    elif normalized_level in {"L1", "L2", "L3"} and pool_id and not watchlist_source_id:
        logger.warning(
            {
                "type": "INVALID_SOURCE_CONFIG",
                "watchlist_id": watchlist_id,
                "level": normalized_level,
                "execution_id": execution_id,
                "message": "source_pool_id is not allowed for L1/L2/L3; ignoring source_pool_id.",
            }
        )
        pool_id = None
    elif normalized_level == "POOL" and watchlist_source_id:
        logger.warning(
            {
                "type": "INVALID_SOURCE_CONFIG",
                "watchlist_id": watchlist_id,
                "level": normalized_level,
                "execution_id": execution_id,
                "message": "source_watchlist_id is not allowed for POOL; ignoring source_watchlist_id.",
            }
        )
        watchlist_source_id = None

    return pool_id, watchlist_source_id


def _intersect_with_upstream(
    *,
    symbols: list[str],
    upstream_symbols: set[str],
    level: str,
    watchlist_id: str,
    execution_id: str,
) -> list[str]:
    if not upstream_symbols:
        return []

    pruned: list[str] = []
    for symbol in symbols:
        if symbol in upstream_symbols:
            pruned.append(symbol)
            continue
        _log_pipeline_event(
            level=level,
            execution_id=execution_id,
            event_type="PIPELINE_VIOLATION",
            watchlist_id=watchlist_id,
            symbol=symbol,
            reason="symbol_not_in_upstream",
        )
    return pruned


def _placeholder_asset_without_market_data(symbol: str) -> dict:
    """Build a minimal asset shell so monitoring boards can list symbols without metadata yet."""
    return {
        "symbol": symbol,
        "name": symbol,
        "price": None,
        "change_24h": None,
        "volume_24h": None,
        "market_cap": None,
        "spread_pct": None,
        "orderbook_depth_usdt": None,
        "indicators": {},
    }


def _build_pipeline_asset(
    symbol: str,
    *,
    name: Optional[str],
    indicators: dict,
    score_row,
    has_market_metadata: bool,
    price=None,
    change_24h=None,
    market_cap=None,
    volume_24h=None,
    spread_pct=None,
    orderbook_depth_usdt=None,
    merged_indicators=None,
) -> dict:
    """Build a normalized pipeline asset dict from metadata, indicators, and scores.

    ``merged_indicators`` (Task #215) is the originating
    :class:`MergedIndicators` object and is stashed under the private
    key ``_merged_indicators`` so :func:`_decision_metrics` can build
    the persisted ``indicators_snapshot``. The leading underscore
    keeps it out of any client-facing serialisation.
    """
    asset = {
        "symbol": symbol,
        "name": name or symbol,
        "price": price,
        "change_24h": change_24h,
        "market_cap": market_cap,
        "volume_24h": volume_24h,
        "spread_pct": spread_pct,
        "orderbook_depth_usdt": orderbook_depth_usdt,
        "indicators": indicators,
        "_has_market_metadata": has_market_metadata,
        "_merged_indicators": merged_indicators,
        **{k: v for k, v in indicators.items() if isinstance(v, (int, float, bool, str))},
    }

    if "atr_pct" in asset and "atr_percent" not in asset:
        asset["atr_percent"] = asset["atr_pct"]

    di_plus = asset.get("di_plus")
    di_minus = asset.get("di_minus")
    if di_plus is not None and di_minus is not None:
        try:
            asset["di_trend"] = float(di_plus) > float(di_minus)
            indicators["di_trend"] = asset["di_trend"]
        except (TypeError, ValueError):
            pass

    if score_row:
        def _score_component(value):
            return float(value) if value is not None else None

        score_fields = {
            "score": float(score_row.score) if score_row.score is not None else 0.0,
            "liquidity_score": _score_component(score_row.liquidity_score),
            "market_structure_score": _score_component(score_row.market_structure_score),
            "momentum_score": _score_component(score_row.momentum_score),
            "signal_score": _score_component(score_row.signal_score),
        }
        asset.update(score_fields)
        indicators.update(score_fields)

    return asset


def _get_redis():
    """Return a Redis client (soft dependency — returns None if unavailable)."""
    try:
        import redis as redis_lib
        from ..config import settings
        return redis_lib.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
        )
    except Exception as exc:
        logger.warning("Pipeline scan: Redis unavailable: %s", exc)
        return None


def _prior_signals(redis, watchlist_id: str) -> set:
    """Load the set of symbols that triggered L3 in the last scan."""
    if not redis:
        return set()
    try:
        raw = redis.get(f"{_REDIS_PREFIX}{watchlist_id}:signals")
        return set(json.loads(raw)) if raw else set()
    except Exception:
        return set()


def _save_signals(redis, watchlist_id: str, symbols: set, ttl: int = 300):
    """Persist the current signal set for the next comparison (TTL 5 min)."""
    if not redis:
        return
    try:
        redis.setex(f"{_REDIS_PREFIX}{watchlist_id}:signals", ttl, json.dumps(list(symbols)))
    except Exception:
        pass


def _prior_decision_states(redis, watchlist_id: str) -> dict:
    """Load the map of {symbol: {state, score, direction, saved_at}} from the last scan."""
    if not redis:
        return {}
    try:
        raw = redis.get(f"{_REDIS_PREFIX}{watchlist_id}:decision_states")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save_decision_states(redis, watchlist_id: str, states: dict, ttl: int = 600):
    """Persist current decision state map for next-cycle comparison (TTL 10 min)."""
    if not redis:
        return
    try:
        redis.setex(f"{_REDIS_PREFIX}{watchlist_id}:decision_states", ttl, json.dumps(states))
    except Exception:
        pass


# ─── L3 visibility (edge-triggered guarantee) ─────────────────────────────────
# Independent of ``decision_states`` (short TTL, transition-based).  Tracks
# which symbols already got at least one decisions_log row in the current
# *presence cycle* at L3 ALLOW.  Pure edge trigger:
#
#     not in L3 → in L3   ⇒ force-log once (event_type = 'L3_VISIBLE')
#     in L3 → in L3       ⇒ skip
#     in L3 → not in L3   ⇒ remove from set (reset)
#     not in L3 → not L3  ⇒ noop
#
# So every symbol *visually shown* in L3 Approved gets at least one row,
# without turning the Audit Trail into a heartbeat.
_L3_VISIBILITY_TTL = 86400  # 24h, refreshed every scan — survives Redis restarts


def _prior_l3_visibility(redis, watchlist_id: str) -> set:
    """Return the set of symbols already logged in the current L3 presence cycle."""
    if not redis:
        return set()
    try:
        raw = redis.smembers(f"{_REDIS_PREFIX}{watchlist_id}:l3_visibility")
        return {s.decode() if isinstance(s, bytes) else s for s in (raw or [])}
    except Exception:
        return set()


def _save_l3_visibility(redis, watchlist_id: str, symbols: set, ttl: int = _L3_VISIBILITY_TTL):
    """Replace the L3 visibility set with the symbols currently in ALLOW state."""
    if not redis:
        return
    key = f"{_REDIS_PREFIX}{watchlist_id}:l3_visibility"
    try:
        pipe = redis.pipeline()
        pipe.delete(key)
        if symbols:
            # Task #310: deterministic ordering before SADD (defensive — SADD itself
            # is set-semantics, but matches the project-wide convention).
            pipe.sadd(key, *sorted(symbols))
            pipe.expire(key, ttl)
        pipe.execute()
    except Exception:
        pass




def _should_log_decision(
    decision: dict,
    prior: Optional[dict],
    score_delta_threshold: float = 5.0,
    direction_change_logs: bool = True,
) -> tuple[bool, Optional[str]]:
    """
    Decide whether a decision dict should be written to the Decision Log and
    return the event_type string.

    Rules:
    - None/absent → ALLOW   → log, NEW_SIGNAL
    - ALLOW       → BLOCK   → log, SIGNAL_LOST
    - BLOCK       → ALLOW   → log, SIGNAL_REGAINED
    - ALLOW → ALLOW stable  → skip
    - ALLOW → ALLOW score delta > threshold → log, SIGNAL_EVOLVED_SCORE
    - ALLOW → ALLOW direction flip         → log, SIGNAL_EVOLVED_DIRECTION (if enabled)
    - BLOCK → BLOCK         → skip
    - None/absent → BLOCK   → skip

    Recovery rule: if prior state is ALLOW but was never confirmed by a successful DB
    write (db_confirmed_at is absent), treat prior as None so the symbol is re-logged.
    This auto-recovers symbols stuck by the ordering bug where Redis advanced ahead of DB.
    """
    current_state = decision.get("decision")
    current_score = float(decision.get("score") or 0)
    current_direction = decision.get("direction")

    # Recovery: unconfirmed ALLOW state means the DB write never happened — reset
    if prior is not None and prior.get("state") == "ALLOW" and not prior.get("db_confirmed_at"):
        prior = None

    if prior is None:
        if current_state == "ALLOW":
            return True, "NEW_SIGNAL"
        return False, None

    prior_state = prior.get("state")
    prior_score = float(prior.get("score") or 0)
    prior_direction = prior.get("direction")

    if prior_state != "ALLOW" and current_state == "ALLOW":
        return True, "SIGNAL_REGAINED"

    if prior_state == "ALLOW" and current_state == "BLOCK":
        return True, "SIGNAL_LOST"

    if prior_state == "ALLOW" and current_state == "ALLOW":
        if abs(current_score - prior_score) > score_delta_threshold:
            return True, "SIGNAL_EVOLVED_SCORE"
        if direction_change_logs and current_direction and prior_direction and current_direction != prior_direction:
            return True, "SIGNAL_EVOLVED_DIRECTION"
        return False, None

    return False, None


async def _update_last_scanned(db, watchlist_id: str):
    """Update last_scanned_at on a PipelineWatchlist after each scan attempt."""
    from sqlalchemy import text
    now = datetime.now(timezone.utc)
    try:
        await db.execute(
            text("UPDATE pipeline_watchlists SET last_scanned_at = :now WHERE id = :wid"),
            {"now": now, "wid": watchlist_id},
        )
        await db.commit()
    except Exception as exc:
        logger.debug("[PipelineScan] Failed to update last_scanned_at for %s: %s", watchlist_id, exc)
        # Reset the session so the next watchlist in the scan loop does not
        # inherit an aborted asyncpg transaction (InFailedSQLTransactionError).
        try:
            await db.rollback()
        except Exception as rb_exc:
            logger.debug("[PipelineScan] Rollback after last_scanned_at failure failed: %s", rb_exc)


# ─── market data loader ───────────────────────────────────────────────────────

async def _fetch_market_data(db, symbols: list) -> list:
    """
    Return a list of asset dicts for the given symbols,
    joining market_metadata + indicators + alpha_scores.
    Mirrors _get_assets_with_indicators from custom_watchlists.py.
    """
    from sqlalchemy import text

    if not symbols:
        return []

    syms_list = list(symbols)

    # Step 1: Fetch market metadata — try with new liquidity columns, fall back if absent
    try:
        meta_rows = (await db.execute(
            text("""
                SELECT
                    m.symbol, m.name,
                    COALESCE(m.market_cap,  pwa.market_cap)  AS market_cap,
                    COALESCE(m.volume_24h,  pwa.volume_24h)  AS volume_24h,
                    m.price,
                    m.price_change_24h,
                    m.spread_pct,
                    m.orderbook_depth_usdt
                FROM market_metadata m
                LEFT JOIN (
                    SELECT DISTINCT ON (symbol)
                           symbol, market_cap, volume_24h
                    FROM   pipeline_watchlist_assets
                    WHERE  symbol = ANY(:symbols)
                    ORDER  BY symbol, entered_at DESC
                ) pwa ON pwa.symbol = m.symbol
                WHERE  m.symbol = ANY(:symbols)
            """),
            {"symbols": syms_list},
        )).fetchall()
    except Exception:
        # Fallback: columns spread_pct / orderbook_depth_usdt may not exist yet
        # asyncpg leaves the transaction aborted after any SQL error. Roll back
        # before the fallback query so the session can still be used.
        try:
            await db.rollback()
        except Exception as rb_exc:
            logger.warning("[PipelineScan] market metadata rollback failed: %s", rb_exc)
            return None
        meta_rows = (await db.execute(
            text("""
                SELECT
                    m.symbol, m.name,
                    COALESCE(m.market_cap, pwa.market_cap) AS market_cap,
                    COALESCE(m.volume_24h, pwa.volume_24h) AS volume_24h,
                    m.price,
                    m.price_change_24h,
                    NULL AS spread_pct,
                    NULL AS orderbook_depth_usdt
                FROM market_metadata m
                LEFT JOIN (
                    SELECT DISTINCT ON (symbol)
                           symbol, market_cap, volume_24h
                    FROM   pipeline_watchlist_assets
                    WHERE  symbol = ANY(:symbols)
                    ORDER  BY symbol, entered_at DESC
                ) pwa ON pwa.symbol = m.symbol
                WHERE  m.symbol = ANY(:symbols)
            """),
            {"symbols": syms_list},
        )).fetchall()

    # Step 2: Fetch indicators via dual-scheduler merge utility
    # Merges structural (15m cadence) + microstructure (5m cadence) rows with
    # per-key latest-timestamp-wins semantics. Falls back to legacy single-row
    # query when scheduler_group column is absent.
    try:
        # Task #215: route through the unified provider so the same merge
        # path + telemetry + quarantine semantics apply across pipeline_scan,
        # evaluate_signals, and execute_buy.
        from ..services.indicators_provider import get_merged_indicators
        _merged_by_sym = await get_merged_indicators(db, syms_list)

        score_rows = (await db.execute(
            text("""
                SELECT DISTINCT ON (symbol)
                    symbol, score,
                    liquidity_score, market_structure_score,
                    momentum_score, signal_score
                FROM alpha_scores
                WHERE symbol = ANY(:symbols)
                  AND time > now() - interval '2 hours'
                ORDER BY symbol, time DESC
            """),
            {"symbols": syms_list},
        )).fetchall()

    except Exception as exc:
        logger.warning("Pipeline scan: market data fetch failed: %s", exc)
        try:
            await db.rollback()
        except Exception as rb_exc:
            logger.warning("[PipelineScan] market data rollback failed: %s", rb_exc)
        return None

    # Build flat ind_map from merged dual-scheduler results
    ind_map = {sym: mi.as_flat_dict() for sym, mi in _merged_by_sym.items()}
    score_map = {r.symbol: r for r in score_rows}

    # ── Funnel stats: symbols requested vs. found in market_metadata ─────────
    requested_set = set(symbols)
    found_meta_set = {r.symbol for r in meta_rows}
    missing_meta = requested_set - found_meta_set
    if missing_meta:
        logger.info(
            "[PipelineScan] market_metadata gap: %d/%d symbols have NO metadata "
            "(sample: %s)",
            len(missing_meta), len(requested_set),
            sorted(missing_meta)[:10],
        )

    # Indicator coverage
    has_indicators = set(ind_map.keys())
    missing_ind = found_meta_set - has_indicators
    if missing_ind:
        logger.info(
            "[PipelineScan] indicator gap: %d/%d symbols with metadata have NO indicators "
            "(sample: %s)",
            len(missing_ind), len(found_meta_set),
            sorted(missing_ind)[:10],
        )

    # Score coverage
    has_scores = set(score_map.keys())
    missing_scores = found_meta_set - has_scores
    if missing_scores:
        logger.debug(
            "[PipelineScan] score gap: %d/%d symbols with metadata have NO alpha_score",
            len(missing_scores), len(found_meta_set),
        )

    assets = []
    for row in meta_rows:
        sym = row.symbol
        indicators = ind_map.get(sym, {})
        score_row  = score_map.get(sym)

        assets.append(_build_pipeline_asset(
            sym,
            name=row.name,
            indicators=indicators,
            score_row=score_row,
            has_market_metadata=True,
            price=float(row.price) if row.price else 0.0,
            change_24h=float(row.price_change_24h) if row.price_change_24h else 0.0,
            market_cap=float(row.market_cap) if row.market_cap is not None else None,
            volume_24h=float(row.volume_24h) if row.volume_24h is not None else None,
            spread_pct=float(row.spread_pct) if row.spread_pct is not None else None,
            orderbook_depth_usdt=float(row.orderbook_depth_usdt) if row.orderbook_depth_usdt is not None else None,
            merged_indicators=_merged_by_sym.get(sym),
        ))

    for sym in sorted(missing_meta):
        indicators = ind_map.get(sym, {})
        score_row = score_map.get(sym)
        assets.append(_build_pipeline_asset(
            sym,
            name=sym,
            indicators=indicators,
            score_row=score_row,
            has_market_metadata=False,
            merged_indicators=_merged_by_sym.get(sym),
        ))

    return assets


# ─── core indicator completeness guard ───────────────────────────────────────

def _filter_incomplete_indicators(assets: list) -> tuple[list, list]:
    """Backward-compatible delegate to the unified provider (Task #215).

    The shared completeness rule + helper now live in
    :mod:`app.services.indicators_provider` so all three Celery tasks
    (pipeline_scan / evaluate_signals / execute_buy) apply identical
    quarantine semantics. The required-core key list is
    ``("adx", "rsi", "macd_histogram")`` — see
    ``indicators_provider.REQUIRED_CORE_INDICATORS`` for the canonical
    rationale and rename procedure.
    """
    from ..services.indicators_provider import filter_incomplete_assets
    return filter_incomplete_assets(assets)


# ─── level evaluators ─────────────────────────────────────────────────────────

def _check_condition_would_fail(cond: dict, actual_value) -> bool:
    """Quick check whether a single filter condition would reject an asset.

    Used only for diagnostic logging — not for actual filtering decisions.
    """
    op_str = cond.get("operator", ">=")
    target = cond.get("value")
    if target is None:
        return False
    try:
        av = float(actual_value) if not isinstance(actual_value, bool) else actual_value
        tv = float(target) if not isinstance(target, bool) else target
        ops = {
            ">=": av >= tv, "<=": av <= tv, ">": av > tv, "<": av < tv,
            "==": av == tv, "=": av == tv, "!=": av != tv,
        }
        return not ops.get(op_str, True)
    except (TypeError, ValueError):
        return False

class _RobustScoreShim:
    """Stand-in for ``ScoreEngine`` used inside ``ProfileEngine`` after
    the Phase 4 cleanup.

    Returns the pre-computed robust score that ``_apply_robust_authoritative_scoring``
    already wrote onto each asset (under ``_score`` / ``alpha_score`` /
    ``score``). The legacy 4-bucket math is *not* executed — keeps the
    robust engine the single source of truth for L2 / L3 gating and for
    every score consumed by signal / entry evaluation.
    """

    def __init__(self, thresholds: Optional[dict] = None) -> None:
        self.thresholds = thresholds or {
            "strong_buy": 80,
            "buy": 65,
            "neutral": 40,
        }

    def _classify(self, score: float) -> str:
        if score >= self.thresholds.get("strong_buy", 80):
            return "strong_buy"
        if score >= self.thresholds.get("buy", 65):
            return "buy"
        if score >= self.thresholds.get("neutral", 40):
            return "neutral"
        return "avoid"

    def compute_score(self, eval_data: dict) -> dict:
        raw = (
            eval_data.get("_score")
            if eval_data.get("_score") is not None
            else eval_data.get("alpha_score")
            if eval_data.get("alpha_score") is not None
            else eval_data.get("score")
        )
        try:
            score = float(raw) if raw is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0
        context = eval_data.get("_score_components") or {}
        component_fields = (
            (context.get("component_fields") or {})
            if isinstance(context, dict) else {}
        )
        components = {
            "liquidity_score": component_fields.get("liquidity_score", 0.0) or 0.0,
            "market_structure_score": component_fields.get("market_structure_score", 0.0) or 0.0,
            "momentum_score": component_fields.get("momentum_score", 0.0) or 0.0,
            "signal_score": component_fields.get("signal_score", 0.0) or 0.0,
            "engine": "robust",
        }
        if isinstance(context, dict):
            components["score_confidence"] = context.get("score_confidence", 0.0) or 0.0
            components["global_confidence"] = context.get("global_confidence", 0.0) or 0.0
        return {
            "total_score": round(score, 2),
            "components": components,
            "matched_rules": (
                context.get("matched_rule_ids", [])
                if isinstance(context, dict) else []
            ),
            "classification": self._classify(score),
        }


def _apply_level_filter(
    assets: list,
    profile_config: Optional[dict],
    level: str,
    score_config: Optional[dict] = None,
    apply_profile_filters: bool = True,
) -> tuple[list, list]:
    """
    Apply ProfileEngine filters for a given level.
    Returns (passed, all_scored).

    score_config: thresholds-only — used to drive the score classification
    bands. The actual numeric score comes from the robust engine
    (``asset["_score"]``) via ``_RobustScoreShim``; legacy ``ScoreEngine``
    rule math is no longer invoked.
    """
    from ..services.profile_engine import ProfileEngine

    engine = ProfileEngine(profile_config)

    # Replace the profile's internal ScoreEngine with a thin shim that
    # returns the asset's pre-computed robust score. This guarantees L2 /
    # L3 gating reads the robust value, not the legacy 4-bucket math.
    engine.score_engine = _RobustScoreShim(
        thresholds=(score_config or {}).get("thresholds")
        or (profile_config or {}).get("scoring", {}).get("thresholds"),
    )

    min_score = 0.0

    # L2: min alpha score gate
    if level == "L2":
        min_score = float((profile_config or {}).get("filters", {}).get("min_score", 0))

    # ── Diagnostic: analyse rejections per filter condition ────────────────
    filter_conditions = (profile_config or {}).get("filters", {}).get("conditions", [])
    if apply_profile_filters and filter_conditions and len(assets) > 0:
        rejection_counts: dict[str, int] = {}
        null_counts: dict[str, int] = {}

        for asset in assets:
            indicators = asset.get("indicators", {})
            flat = {**asset, **{k: v for k, v in indicators.items() if isinstance(v, (int, float, bool, str))}}
            for cond in filter_conditions:
                field = cond.get("field", "")
                if not field:
                    continue
                val = flat.get(field)
                if val is None:
                    null_counts[field] = null_counts.get(field, 0) + 1
                    if field in _DIAG_STRICT_META:
                        rejection_counts[field + " (NULL→FAIL)"] = rejection_counts.get(field + " (NULL→FAIL)", 0) + 1
                else:
                    if _check_condition_would_fail(cond, val):
                        rejection_counts[field] = rejection_counts.get(field, 0) + 1

        # Task #232 — publish per-reason rejection_rate so dashboards
        # can break down the profile_filter stage by which condition
        # rejected the candidate (was previously only a log line).
        try:
            from ..services.execution_gate_metrics import record_pipeline_rejection_reason
            entered = len(assets)
            for reason, cnt in rejection_counts.items():
                record_pipeline_rejection_reason("profile_filter", reason, cnt, entered)
        except Exception as exc:
            logger.debug("[PipelineScan] rejection-reason metrics failed: %s", exc)

        if rejection_counts or null_counts:
            logger.info(
                "[PipelineScan] %s filter diagnostics (%d assets):\n"
                "  NULL fields: %s\n"
                "  Rejection causes: %s",
                level, len(assets),
                {k: f"{v}/{len(assets)}" for k, v in sorted(null_counts.items(), key=lambda x: -x[1])},
                {k: f"{v}/{len(assets)}" for k, v in sorted(rejection_counts.items(), key=lambda x: -x[1])},
            )

    # Apply structural filters.
    # strict_indicators=True: assets with missing indicator data FAIL indicator
    # conditions rather than skipping them.  This prevents assets that have never
    # had indicators computed (e.g. newly-added pool coins) from bypassing EMA /
    # RSI / ADX conditions and incorrectly appearing in pipeline stages.
    filtered = (
        engine._apply_filters(assets, strict_indicators=True)
        if apply_profile_filters
        else list(assets)
    )

    if apply_profile_filters:
        logger.info(
            "[PipelineScan] %s profile filters: %d → %d assets (rejected %d)",
            level, len(assets), len(filtered), len(assets) - len(filtered),
        )
    else:
        logger.info(
            "[PipelineScan] %s monitoring mode: keeping all %d assets visible (profile filters bypassed)",
            level, len(filtered),
        )

    # Compute scores for all passing assets
    scored = []
    below_min_score = 0
    for asset in filtered:
        processed = engine._process_single_asset(asset, include_details=True)
        total = processed.get("score", {}).get("total_score", 0)
        if total >= min_score:
            scored.append({**asset, "_score": total, "_processed": processed})
        else:
            below_min_score += 1

    if below_min_score:
        logger.info(
            "[PipelineScan] %s min_score gate (%.1f): rejected %d/%d filtered assets",
            level, min_score, below_min_score, len(filtered),
        )

    return scored, filtered


def _evaluate_l3_signals(assets: list, profile_config: Optional[dict], score_config: Optional[dict] = None) -> list:
    """
    Apply L3 signal conditions and return triggered assets.

    If the profile has NO signal conditions configured, fall back to scoring-only
    mode: return all assets that passed the profile filters, sorted by score.
    This prevents L3 from being permanently empty just because no signal conditions
    have been set up yet.

    Score values come from the robust engine via ``_RobustScoreShim``.
    """
    from ..services.profile_engine import ProfileEngine

    engine = ProfileEngine(profile_config)
    engine.score_engine = _RobustScoreShim(
        thresholds=(score_config or {}).get("thresholds")
        or (profile_config or {}).get("scoring", {}).get("thresholds"),
    )

    # Check if the profile has any signal conditions at all.
    # Signal conditions may be stored under 'entry_triggers' OR 'signals'.
    sig_conditions = (
        (profile_config or {}).get("entry_triggers", {}).get("conditions") or
        (profile_config or {}).get("signals", {}).get("conditions") or
        []
    )
    has_signal_conditions = bool(sig_conditions)

    result = engine.process_watchlist(assets, include_details=True)

    if has_signal_conditions:
        # Signal evaluation mode: only return assets with triggered signals
        signals = []
        for asset in result.get("assets", []):
            sig = asset.get("signal", {})
            if sig.get("triggered"):
                signals.append({
                    "symbol":             asset["symbol"],
                    "score":              asset.get("score", {}).get("total_score", 0),
                    "price":              asset.get("price", 0),
                    "change_24h":         asset.get("change_24h", 0),
                    "volume_24h":         asset.get("volume_24h"),
                    "market_cap":         asset.get("market_cap"),
                    "matched_conditions": sig.get("matched_conditions", []),
                })
        signals.sort(key=lambda x: x["score"], reverse=True)
        return signals
    else:
        # No signal conditions — fall back to scoring mode: return all filtered
        # assets sorted by score (same as L2 behavior)
        logger.info(
            "[PipelineScan] L3: no signal conditions in profile — using scoring fallback (%d assets)",
            len(result.get("assets", [])),
        )
        fallback = []
        for asset in result.get("assets", []):
            total = asset.get("score", {}).get("total_score", 0)
            fallback.append({
                "symbol":             asset["symbol"],
                "score":              total,
                "price":              asset.get("price", 0),
                "change_24h":         asset.get("change_24h", 0),
                "volume_24h":         asset.get("volume_24h"),
                "market_cap":         asset.get("market_cap"),
                "matched_conditions": [],
            })
        fallback.sort(key=lambda x: x["score"], reverse=True)
        return fallback


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _decision_reason_map(processed: dict, has_signal_conditions: bool) -> dict:
    reasons: dict[str, str] = {}

    evaluation = processed.get("_evaluation") or {}
    for rule in evaluation.get("score_matched_rules") or []:
        reason_key = None
        if isinstance(rule, dict):
            reason_key = rule.get("indicator") or rule.get("id")
        elif isinstance(rule, str):
            reason_key = rule
        elif isinstance(rule, (int, float)):
            reason_key = str(rule)
        if isinstance(reason_key, str) and reason_key:
            reasons[reason_key] = "OK"

    signal = processed.get("signal") or {}
    for matched in signal.get("matched_conditions", []):
        reasons[str(matched)] = "OK"
    for failed in signal.get("failed_required", []):
        reasons[str(failed)] = "FAIL"

    entry = processed.get("entry") or {}
    for matched in entry.get("matched", []):
        reasons[str(matched)] = "OK"
    for failed in entry.get("failed_required", []):
        reasons[str(failed)] = "FAIL"

    if processed.get("blocked"):
        # Block rules use circuit-breaker vocabulary (Task #253): TRIPPED
        # means the block fired and the asset is rejected. Filters /
        # signals / entry triggers keep PASS/FAIL — their polarity is
        # positive so the legacy vocabulary still reads naturally.
        reasons["block_rules"] = "TRIPPED"
    if processed.get("passed_filter") is False:
        for failed in processed.get("filter_failed", []):
            reasons[str(failed)] = "FAIL"
    if not has_signal_conditions and processed.get("passed_filter"):
        reasons.setdefault("scoring_fallback", "OK")

    if not reasons:
        reasons["pipeline"] = "OK" if processed.get("passed_filter") else "FAIL"
    return reasons


def _decision_metrics(asset: dict, processed: dict) -> dict:
    score = processed.get("score", {}) or {}
    robust_context = asset.get("_score_components") or {}
    component_fields = (
        (robust_context.get("component_fields") or {})
        if isinstance(robust_context, dict) else {}
    )
    flat_indicators = asset.get("indicators") or {}
    if not component_fields and isinstance(flat_indicators, dict):
        component_fields = {
            key: flat_indicators.get(key)
            for key in (
                "liquidity_score",
                "market_structure_score",
                "momentum_score",
                "signal_score",
            )
            if flat_indicators.get(key) is not None
        }
    score_components = (
        score.get("components")
        or component_fields
        or {}
    )
    metrics = {
        **(asset.get("indicators") or {}),
        "price": asset.get("price"),
        "change_24h": asset.get("change_24h"),
        "volume_24h": asset.get("volume_24h"),
        "market_cap": asset.get("market_cap"),
        "score_components": score_components,
        "score_classification": score.get("classification"),
        "signal_direction": (processed.get("signal") or {}).get("direction"),
    }

    # Task #215 / P0-empty-metrics fix:
    #
    # indicators_snapshot MUST always be present in metrics so that
    # _build_features_snapshot (shadow_trade_service) always produces a
    # non-empty features_snapshot on shadow trades. Two gaps fixed here:
    #
    # Gap A — missing snapshot: the original code only added
    #   indicators_snapshot when _merged_indicators was non-None. While the
    #   quarantine guard should prevent None reaching here, a defensive
    #   fallback is cheaper than debugging a silent empty-features_snapshot.
    #
    # Gap B — live injection skew: _inject_live_order_flow overwrites
    #   asset["indicators"] with live values for taker_ratio / volume_delta
    #   / spread_pct / vwap_distance_pct etc. BEFORE the decision is made,
    #   but build_indicators_snapshot reads from merged.values (pre-injection
    #   DB snapshot). The persisted snapshot therefore doesn't match the
    #   values that actually drove the decision — a train-serve skew source.
    #   Fix: after building from merged, overlay asset["indicators"] for any
    #   live-injection field so the snapshot is always decision-time-accurate.
    merged = asset.get("_merged_indicators")

    if merged is not None:
        from ..services.indicators_provider import build_indicators_snapshot
        consumed_keys: set[str] = set()
        components = score.get("components") or {}
        if isinstance(components, dict):
            for comp_value in components.values():
                if isinstance(comp_value, dict):
                    # Component contributions can be either {"value": …,
                    # "indicator": "rsi"} or nested {"rsi": …, "adx": …}
                    if "indicator" in comp_value and isinstance(comp_value["indicator"], str):
                        consumed_keys.add(comp_value["indicator"])
                    consumed_keys.update(
                        k for k in comp_value.keys()
                        if isinstance(k, str) and k in (merged.values or {})
                    )
        snapshot = build_indicators_snapshot(merged, keys=consumed_keys)

        # Gap B fix: overlay live-injected values onto snapshot entries so the
        # persisted snapshot matches what the decision engine actually saw.
        # Only keys present in _LIVE_ORDER_FLOW_FIELDS are candidates — those
        # are the only fields that _inject_live_order_flow can override.
        for key in _LIVE_ORDER_FLOW_FIELDS:
            if key in flat_indicators:
                live_val = flat_indicators[key]
                if key in snapshot:
                    # Preserve existing metadata (source_group, ts, stale) but
                    # overwrite value to match the live-injected value that drove
                    # the decision.
                    if snapshot[key].get("value") != live_val:
                        snapshot[key] = {
                            **snapshot[key],
                            "value": live_val,
                            "source_group": "live_injection",
                        }
                else:
                    snapshot[key] = {"value": live_val, "source_group": "live_injection"}

        for key in _DECISION_CONTEXT_SNAPSHOT_FIELDS:
            if key in component_fields and component_fields.get(key) is not None:
                snapshot[key] = {
                    "value": component_fields.get(key),
                    "source_group": "decision_context",
                }
            elif key in asset and asset.get(key) is not None:
                snapshot[key] = {
                    "value": asset.get(key),
                    "source_group": "decision_context",
                }
            elif key in flat_indicators and flat_indicators.get(key) is not None:
                snapshot[key] = {
                    "value": flat_indicators.get(key),
                    "source_group": "decision_context",
                }

        metrics["indicators_snapshot"] = snapshot

    else:
        # Gap A fallback: merged unavailable (quarantine should prevent this,
        # but be defensive). Build snapshot from flat indicators so
        # features_snapshot is never empty. _build_features_snapshot handles
        # both {"value": v} dict form and flat scalar form.
        metrics["indicators_snapshot"] = {
            k: {"value": v, "source_group": "fallback_no_merged"}
            for k, v in flat_indicators.items()
            if isinstance(v, (int, float, bool, type(None)))
        }
        for key in _DECISION_CONTEXT_SNAPSHOT_FIELDS:
            if key in component_fields and component_fields.get(key) is not None:
                metrics["indicators_snapshot"][key] = {
                    "value": component_fields.get(key),
                    "source_group": "decision_context",
                }
            elif key in asset and asset.get(key) is not None:
                metrics["indicators_snapshot"][key] = {
                    "value": asset.get(key),
                    "source_group": "decision_context",
                }

    return _jsonable(metrics)


async def _inject_live_order_flow(
    *,
    symbol: str,
    indicators: dict,
    db,
    user_id,
    pool_id,
) -> tuple[dict, bool]:
    """Sobrescrever indicadores de fluxo do ``indicators`` (DB) com o
    snapshot ao vivo do buffer Redis antes da regra L3 avaliar.

    Retorna ``(updated_indicators, ok)``:
      * ``updated_indicators`` — cópia rasa de ``indicators`` com as
        chaves de :data:`_LIVE_ORDER_FLOW_FIELDS` substituídas pelos
        valores do live, quando o respectivo valor live é não-nulo.
        Quando o live é indisponível (buffer + REST falham), retorna o
        ``indicators`` original (fail-soft: nunca pior que o estado
        atual baseado em DB).
      * ``ok`` — ``True`` quando a decisão pode prosseguir; ``False``
        APENAS quando o snapshot live foi obtido mas o trade mais
        recente excede ``l3_order_flow_max_age_seconds``. Caller deve
        usar ``continue`` (skip do ciclo, re-tentada no próximo).

    Falhas (config_service indisponível, exceção ao ler Redis,
    get_order_flow_data raise) NUNCA bloqueiam a decisão — log
    estruturado + fallback ao DB. O bloqueio por idade é a ÚNICA
    decisão "fail-closed" desta função.

    Configuração (Zero Hardcode):
      * ``l3_order_flow_window_seconds`` (default :data:`_ORDER_FLOW_WINDOW_DEFAULT`)
      * ``l3_order_flow_max_age_seconds`` (default :data:`_ORDER_FLOW_MAX_AGE_DEFAULT`,
        valor ``<= 0`` desabilita o gate de idade)

    Lidos via ``ConfigProfile(config_type="pipeline")``; pool-scoped
    quando ``pool_id`` é não-nulo, senão global por usuário.
    """
    from ..services.config_service import config_service
    from ..services.order_flow_service import get_order_flow_data

    # ── Config (best-effort) ─────────────────────────────────────────────
    window = _ORDER_FLOW_WINDOW_DEFAULT
    max_age = _ORDER_FLOW_MAX_AGE_DEFAULT
    try:
        cfg = await config_service.get_config(
            db, "pipeline", user_id, pool_id=pool_id,
        )
        if isinstance(cfg, dict):
            window = int(cfg.get(_ORDER_FLOW_WINDOW_CONFIG_KEY, window))
            max_age = int(cfg.get(_ORDER_FLOW_MAX_AGE_CONFIG_KEY, max_age))
    except Exception as exc:
        logger.warning(
            "[L3][%s] live_order_flow config read failed (%s) — using defaults",
            symbol, exc,
        )

    # ── Live snapshot ────────────────────────────────────────────────────
    try:
        live = await get_order_flow_data(symbol=symbol, window_seconds=window)
    except Exception as exc:
        logger.error(
            "[L3][%s] live_order_flow fetch failed (%r) — falling back to DB",
            symbol, exc,
        )
        return indicators, True

    if not isinstance(live, dict):
        logger.warning("[L3][%s] live_order_flow returned non-dict — fallback", symbol)
        return indicators, True

    # ── Stale guard ──────────────────────────────────────────────────────
    age = live.get("data_age_seconds")
    if age is not None and max_age > 0 and age > max_age:
        logger.warning(
            "[L3][%s] live_order_flow STALE: age=%.1fs > max=%ds — blocking decision this cycle",
            symbol, age, max_age,
        )
        return indicators, False

    # ── Merge (live wins quando não-nulo) ─────────────────────────────────
    updated = dict(indicators) if isinstance(indicators, dict) else {}
    overridden: list[str] = []
    for asset_key, live_key in _LIVE_ORDER_FLOW_FIELDS.items():
        live_val = live.get(live_key)
        if live_val is not None:
            updated[asset_key] = live_val
            overridden.append(asset_key)

    logger.debug(
        "[L3][%s] live_order_flow injected age=%s window=%ds source=%s overrode=%s",
        symbol, age, window, live.get("taker_source"), overridden,
    )
    return updated, True


async def _evaluate_l3_decisions(
    assets: list,
    profile_config: Optional[dict],
    strategy_level: str,
    score_config: Optional[dict] = None,
    *,
    db=None,
    user_id=None,
    pool_id=None,
) -> list[dict]:
    """Avaliar candidatos L3 (rules + entry triggers) e produzir decisions.

    Quando ``db``, ``user_id`` recebidos: para cada asset, sobrescrevemos
    indicadores de fluxo (taker_*, buy_pressure, volume_delta) com o
    snapshot live do Redis ANTES de avaliar a regra (Task: live order
    flow injection). Sem esses parâmetros (call sites legados), o
    comportamento é o original (lê só do DB).
    """
    from ..services.profile_engine import ProfileEngine

    engine = ProfileEngine(profile_config)
    engine.score_engine = _RobustScoreShim(
        thresholds=(score_config or {}).get("thresholds")
        or (profile_config or {}).get("scoring", {}).get("thresholds"),
    )

    sig_conditions = (
        (profile_config or {}).get("entry_triggers", {}).get("conditions")
        or (profile_config or {}).get("signals", {}).get("conditions")
        or []
    )
    has_signal_conditions = bool(sig_conditions)
    timeframe = (profile_config or {}).get("default_timeframe", "5m")

    inject_live = db is not None and user_id is not None
    decisions: list[dict] = []
    for asset in assets:
        # ── L3 LIVE ORDER FLOW INJECTION ─────────────────────────────────
        # Sobrescreve indicators de fluxo com snapshot live do Redis
        # antes da regra avaliar. Stale → continue (skip do ciclo).
        if inject_live:
            symbol = asset.get("symbol")
            current_indicators = asset.get("indicators") or {}
            updated_indicators, order_flow_ok = await _inject_live_order_flow(
                symbol=symbol,
                indicators=current_indicators,
                db=db,
                user_id=user_id,
                pool_id=pool_id,
            )
            if not order_flow_ok:
                logger.info(
                    "[L3][%s] skipped this cycle (stale order flow) — will retry next tick",
                    symbol,
                )
                continue
            # Re-flatten escalares no top-level do asset (mesmo padrão
            # de ``_build_pipeline_asset``) para que ``ProfileEngine``
            # leia as overrides via ``asset[key]`` quando aplicável.
            asset["indicators"] = updated_indicators
            for k, v in updated_indicators.items():
                if isinstance(v, (int, float, bool, str)) and k in _LIVE_ORDER_FLOW_FIELDS:
                    asset[k] = v

        started_at = datetime.now(timezone.utc)
        processed = engine.evaluate_asset(asset)
        latency_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
        score = (processed.get("score") or {}).get("total_score", 0)
        decision = "BLOCK"
        l3_pass = False

        if not processed.get("blocked") and processed.get("passed_filter", False):
            if has_signal_conditions:
                l3_pass = bool((processed.get("signal") or {}).get("triggered"))
                decision = "ALLOW" if l3_pass else "BLOCK"
            else:
                l3_pass = True
                decision = "ALLOW"

        decisions.append({
            "symbol": asset.get("symbol"),
            "strategy": strategy_level,
            "timeframe": timeframe,
            "score": score,
            "decision": decision,
            "l1_pass": True,
            "l2_pass": True,
            "l3_pass": l3_pass,
            "reasons": _decision_reason_map(processed, has_signal_conditions),
            "metrics": _decision_metrics(asset, processed),
            "latency_ms": latency_ms,
            # Defesa em profundidade (Task #292): se _apply_robust_authoritative_scoring
            # não rodou (fallback path) ou um caller futuro montar dict sem
            # passar pelo setter, ainda gravamos valor canônico em vez de NULL.
            # Pool atual é spot-only; ``is_futures`` flag determina o default.
            "direction": (
                asset.get("futures_direction")
                or ("NEUTRAL" if asset.get("is_futures") else "SPOT")
            ),
            "created_at": datetime.now(timezone.utc),
            "_processed": processed,
            "_asset": asset,
        })

    return decisions


async def _persist_decision_logs(db, user_id, decisions: list[dict]):
    from ..models.backoffice import DecisionLog
    from sqlalchemy import or_, and_, select

    if not decisions:
        return []

    # DEDUPLICATION: Check for recent duplicate decisions (last 5 minutes).
    # Previously used raw SQL ``(col1, col2) IN :checks`` which is not
    # supported by asyncpg's parameter binding and could silently match nothing,
    # allowing duplicate rows on Redis restarts. Replaced with ORM-native
    # ``or_()`` conditions which are always correctly parameterised.
    now = datetime.now(timezone.utc)
    recent_window = now - timedelta(minutes=5)

    dedup_checks = []
    for decision in decisions:
        dedup_checks.append((
            decision["symbol"],
            decision["strategy"],
            decision.get("direction"),
            decision.get("_profile_id"),
        ))

    if dedup_checks:
        unique_checks = list(set(dedup_checks))

        # Build ORM-safe OR clause: each tuple becomes an AND condition.
        #
        # Profile identity is part of the dedup key. Multiple L3 watchlists
        # evaluate the same symbol in the same scan cycle; collapsing their
        # decisions by symbol/strategy/direction prevents Profile Intelligence
        # candidates from receiving profile-attributed shadow trades.
        row_conditions = []
        for s, st, d, profile_id in unique_checks:
            dir_cond = (DecisionLog.direction == d) if d is not None else DecisionLog.direction.is_(None)
            profile_cond = (
                (DecisionLog.profile_id == profile_id)
                if profile_id is not None
                else DecisionLog.profile_id.is_(None)
            )
            row_conditions.append(and_(
                DecisionLog.symbol == s,
                DecisionLog.strategy == st,
                dir_cond,
                profile_cond,
            ))

        existing_result = await db.execute(
            select(
                DecisionLog.symbol,
                DecisionLog.strategy,
                DecisionLog.direction,
                DecisionLog.profile_id,
            )
            .where(and_(
                DecisionLog.user_id == user_id,
                DecisionLog.created_at >= recent_window,
                or_(*row_conditions),
            ))
            .distinct()
        )

        existing_decisions = {
            (
                row.symbol,
                row.strategy,
                row.direction or None,
                row.profile_id,
            )
            for row in existing_result.fetchall()
        }

        decisions_to_insert = []
        skipped_count = 0
        for decision in decisions:
            key = (
                decision["symbol"],
                decision["strategy"],
                decision.get("direction"),
                decision.get("_profile_id"),
            )
            if (decision.get("reasons") or {}).get("ml_gate"):
                decisions_to_insert.append(decision)
            elif key in existing_decisions:
                logger.debug(
                    "[Decision] SKIP duplicate: %s | strategy=%s | direction=%s (logged in last 5 min)",
                    key[0], key[1], key[2] or "—",
                )
                skipped_count += 1
            else:
                decisions_to_insert.append(decision)

        if skipped_count > 0:
            logger.info(
                "[Decision] dedup_conflict: skipped %d duplicate(s), inserting %d new decision(s)",
                skipped_count, len(decisions_to_insert),
            )

        decisions = decisions_to_insert

    if not decisions:
        return []

    rows = []
    for decision in decisions:
        m = decision.get("metrics") or {}
        if not m or not m.get("indicators_snapshot"):
            logger.warning(
                "[Decision] METRICS_EMPTY symbol=%s decision=%s — "
                "indicators_snapshot absent; shadow features_snapshot will be empty. "
                "Check _decision_metrics / _inject_live_order_flow path.",
                decision.get("symbol"), decision.get("decision"),
            )
        rows.append(DecisionLog(
            symbol=decision["symbol"],
            strategy=decision["strategy"],
            timeframe=decision.get("timeframe"),
            score=decision.get("score"),
            decision=decision["decision"],
            l1_pass=decision.get("l1_pass"),
            l2_pass=decision.get("l2_pass"),
            l3_pass=decision.get("l3_pass"),
            reasons=decision.get("reasons"),
            metrics=m or None,
            latency_ms=decision.get("latency_ms"),
            direction=decision.get("direction"),
            event_type=decision.get("event_type"),
            user_id=user_id,
            created_at=decision.get("created_at"),
            profile_id=decision.get("_profile_id"),
            profile_name=decision.get("_profile_name"),
            profile_version=decision.get("_profile_version"),
            ranking_id=_uuid_or_none(decision.get("ranking_id")),
            model_id=_uuid_or_none(decision.get("model_id")),
            model_version=decision.get("model_version"),
            model_lane=decision.get("model_lane"),
            probability=decision.get("probability"),
            threshold_used=decision.get("threshold_used"),
            score_status=decision.get("score_status"),
            gate_action=decision.get("gate_action"),
            reason_codes=decision.get("reason_codes"),
            orchestrator_payload=decision.get("orchestrator_payload"),
            ml_gate_enabled=bool(decision.get("ml_gate_enabled", False)),
        ))
    db.add_all(rows)
    await db.flush()

    payloads = []
    for row in rows:
        payload = {
            "id": row.id,
            "symbol": row.symbol,
            "strategy": row.strategy,
            "timeframe": row.timeframe,
            "score": row.score,
            "decision": row.decision,
            "l1_pass": row.l1_pass,
            "l2_pass": row.l2_pass,
            "l3_pass": row.l3_pass,
            "reasons": row.reasons or {},
            "metrics": row.metrics or {},
            "latency_ms": row.latency_ms,
            "direction": row.direction,
            "event_type": row.event_type,
            "ranking_id": str(row.ranking_id) if row.ranking_id else None,
            "model_id": str(row.model_id) if row.model_id else None,
            "model_version": row.model_version,
            "model_lane": row.model_lane,
            "probability": row.probability,
            "threshold_used": row.threshold_used,
            "score_status": row.score_status,
            "gate_action": row.gate_action,
            "reason_codes": row.reason_codes or [],
            "orchestrator_payload": row.orchestrator_payload or {},
            "ml_gate_enabled": row.ml_gate_enabled,
            "created_at": row.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        logger.info(
            "[Decision] PERSISTED | id=%s | %s | score=%s | %s | event=%s",
            row.id, row.symbol, round(float(row.score or 0), 2), row.decision, row.event_type or "—",
        )
        payloads.append(payload)

    # Log summary
    logger.info(
        "[Decision] Batch persisted: %d decision(s) successfully logged to decisions_log table",
        len(payloads)
    )

    return payloads


async def _run_staleness_only(
    db,
    watchlist_id: str,
    filters_json: dict | None = None,
    execution_id: Optional[str] = None,
):
    """Run ONLY staleness expiry + cleanup — no active/down marking.

    Called when a pipeline scan cannot fetch market data, so we don't want to
    wipe the watchlist. Instead, we only expire assets whose refreshed_at is
    older than staleness_minutes (default 30 min).
    """
    from sqlalchemy import text
    now = datetime.now(timezone.utc)

    staleness_minutes = int((filters_json or {}).get("staleness_minutes", _DEFAULT_STALENESS_MINUTES))
    staleness_cutoff = now - timedelta(minutes=staleness_minutes)
    stale_result = await db.execute(text("""
        UPDATE pipeline_watchlist_assets
        SET level_direction = 'down',
            level_change_at = :now,
            execution_id = :execution_id
        WHERE watchlist_id = :wid
          AND level_direction IS NULL
          AND refreshed_at IS NOT NULL
          AND refreshed_at < :cutoff
        RETURNING symbol
    """), {"wid": watchlist_id, "now": now, "cutoff": staleness_cutoff, "execution_id": execution_id})
    stale_rows = stale_result.fetchall()
    if stale_rows:
        logger.info(
            "[PipelineScan] Staleness-only expiry (%d min): marked %d assets as 'down' "
            "in watchlist %s (no market data): %s",
            staleness_minutes, len(stale_rows), watchlist_id,
            [r.symbol for r in stale_rows],
        )

    # Cleanup: remove 'down' records older than 2h
    await db.execute(text("""
        DELETE FROM pipeline_watchlist_assets
        WHERE watchlist_id = :wid
          AND level_direction = 'down'
          AND level_change_at < now() - interval '2 hours'
    """), {"wid": watchlist_id})

    await db.commit()


# NOTE: ``_robust_futures_direction_bias`` was lifted into
# ``app.services.robust_indicators.asset_score.robust_futures_direction_bias``
# in Phase 4 so the API drilldown panels can import it without depending on
# the Celery task module. This shim is kept only for historical references.
from ..services.robust_indicators import (
    robust_futures_direction_bias as _robust_futures_direction_bias,
)


async def _apply_robust_authoritative_scoring(
    assets: list,
    *,
    score_config: dict | None,
    is_futures: bool,
    db=None,
    user_id=None,
    watchlist_id=None,
) -> dict[str, int]:
    """Apply the authoritative robust score to every asset.

    Mutates each asset dict in place, persists the per-symbol envelope
    snapshot to ``indicator_snapshots`` (best-effort), and emits the
    standard robust-engine metrics.

      * Always sets ``engine_tag = "robust"`` so the upsert path can
        persist the column for the asset row.
      * For robust-succeeded assets, overrides ``_score`` /
        ``alpha_score`` (spot) and ``confidence_score`` /
        ``score_long`` / ``score_short`` (futures) with the robust
        score. For futures, the LONG / SHORT split is derived from
        indicator envelopes via ``_robust_futures_direction_bias`` —
        legacy score columns are never read.
      * When the robust engine cannot produce a value (missing
        indicators, validation failure, etc.) every score column is
        zeroed so a pre-existing legacy numeric value is never
        persisted and the symbol is naturally suppressed downstream.

    Returns counters: ``{"bucketed", "robust_used", "fallbacks"}``
    so the caller can log a single "rollout summary" line per scan.
    """
    from ..services.robust_indicators import (
        calculate_score_with_confidence,
        envelope_indicators,
        normalize_component_scores,
        persist_snapshot,
        score_component_fields,
        validate_indicator_integrity,
    )
    from ..services.robust_indicators.metrics import (
        increment_rejection,
        set_indicator_confidence,
        set_indicator_staleness,
    )
    from ..services.seed_service import DEFAULT_SCORE

    counters = {"bucketed": 0, "robust_used": 0, "fallbacks": 0}
    if not assets:
        return counters

    rules = (
        (score_config or {}).get("scoring_rules")
        or (score_config or {}).get("rules")
        or DEFAULT_SCORE.get("scoring_rules")
        or []
    )

    for asset in assets:
        symbol = asset.get("symbol")
        if not symbol:
            continue

        indicators = asset.get("indicators") or {}
        flow_hint = (
            indicators.get("taker_source")
            if isinstance(indicators, dict) else None
        )
        asset["engine_tag"] = "robust"
        # Task #292: propagar is_futures (parâmetro da função) para o asset
        # de modo que o fallback em ``_evaluate_l3_decisions`` consiga
        # decidir entre 'SPOT' e 'NEUTRAL' mesmo quando esta função não
        # roda até o fim (fallback path, exceções, etc).
        asset["is_futures"] = bool(is_futures)
        counters["bucketed"] += 1

        envelopes = None
        validation = None
        result = None
        new_score: float | None = None

        try:
            envelopes = envelope_indicators(
                str(symbol), indicators, flow_source_hint=flow_hint,
            )
            validation = validate_indicator_integrity(envelopes)
            result = calculate_score_with_confidence(envelopes, rules)
            if not result.rejected and result.score is not None:
                new_score = float(result.score)
        except Exception as exc:
            logger.debug(
                "[PipelineScan] robust score failed for %s: %s",
                symbol, exc,
            )

        # Emit retained Phase 1 metrics for every symbol with envelopes.
        if envelopes:
            try:
                if result is not None:
                    set_indicator_confidence(
                        str(symbol), float(result.global_confidence)
                    )
                for name, env in envelopes.items():
                    set_indicator_staleness(
                        str(symbol), name, float(env.staleness_seconds or 0.0)
                    )
            except Exception:
                pass

        if result is not None and result.rejected and result.rejection_reason:
            try:
                reason_key = result.rejection_reason.split(":", 1)[0]
                increment_rejection(reason_key)
            except Exception:
                pass

        if result is not None:
            component_scores = normalize_component_scores(
                rules, getattr(result, "components", {}) or {}
            )
            component_fields = score_component_fields(component_scores)
            matched_rule_ids = [
                str(rule.get("rule_id"))
                for rule in (getattr(result, "matched_rules", []) or [])
                if isinstance(rule, dict) and rule.get("rule_id") is not None
            ]
            asset["_score_components"] = {
                "engine": "robust",
                "components": dict(getattr(result, "components", {}) or {}),
                "component_scores": component_scores,
                "component_fields": component_fields,
                "score_confidence": float(
                    getattr(result, "score_confidence", 0.0) or 0.0
                ),
                "global_confidence": float(
                    getattr(result, "global_confidence", 0.0) or 0.0
                ),
                "matched_rule_ids": matched_rule_ids,
                "evaluated_rule_ids": list(getattr(result, "evaluated_rule_ids", []) or []),
            }
            for field, value in component_fields.items():
                if value is None:
                    continue
                asset[field] = value
                if isinstance(indicators, dict):
                    indicators[field] = value

        # Best-effort snapshot persistence for ops visibility. Skipped
        # silently when the caller did not pass a session — keeps the
        # function callable from places that lack a DB handle.
        if (
            db is not None
            and envelopes is not None
            and validation is not None
            and result is not None
        ):
            try:
                await persist_snapshot(
                    db,
                    symbol=str(symbol),
                    envelopes=envelopes,
                    validation=validation,
                    score=result,
                    user_id=user_id,
                    watchlist_id=watchlist_id,
                )
            except Exception as exc:
                logger.debug(
                    "[PipelineScan] snapshot persist failed for %s: %s",
                    symbol, exc,
                )

        if new_score is None:
            counters["fallbacks"] += 1
            asset["_score"] = 0.0
            asset["score"] = 0.0
            asset["alpha_score"] = 0.0
            if is_futures:
                asset["confidence_score"] = 0.0
                if asset.get("score_long") is not None:
                    asset["score_long"] = 0.0
                if asset.get("score_short") is not None:
                    asset["score_short"] = 0.0
            continue

        counters["robust_used"] += 1

        if is_futures:
            asset["confidence_score"] = round(new_score, 2)
            bias = _robust_futures_direction_bias(indicators)
            long_mult = 1.0 - max(0.0, -bias)
            short_mult = 1.0 - max(0.0, bias)
            asset["score_long"] = round(
                max(0.0, min(100.0, new_score * long_mult)), 2
            )
            asset["score_short"] = round(
                max(0.0, min(100.0, new_score * short_mult)), 2
            )
            # Direction tag derived from the robust bias — replaces the
            # legacy ``futures_direction`` field that used to come from
            # ``score_futures``. Threshold mirrors the spirit of the old
            # ``direction_gap_min`` (small bias → NEUTRAL).
            if bias >= 0.1:
                asset["futures_direction"] = "LONG"
            elif bias <= -0.1:
                asset["futures_direction"] = "SHORT"
            else:
                asset["futures_direction"] = "NEUTRAL"
            asset.setdefault("block_both", False)
            asset.setdefault("entry_long_blocked", False)
            asset.setdefault("entry_short_blocked", False)
        else:
            # SPOT path (Task #292, Bug B fix): spot é long-only por
            # natureza — qualquer ALLOW spot é semanticamente uma
            # "compra". Setar 'SPOT' aqui garante que
            # ``decisions_log.direction`` seja populado (antes ficava
            # NULL no path SPOT, o que travava o gate Shadow Portfolio
            # que filtra por direction IN ('SPOT','LONG')).
            asset["futures_direction"] = "SPOT"
        asset["_score"] = new_score
        asset["score"] = new_score
        asset["alpha_score"] = new_score

    return counters


async def _replace_rejection_snapshot(
    db,
    watchlist_id: str,
    user_id,
    profile_id,
    rows: list[dict],
    execution_id: Optional[str] = None,
):
    from sqlalchemy import text

    await db.execute(
        text("DELETE FROM pipeline_watchlist_rejections WHERE watchlist_id = :wid"),
        {"wid": watchlist_id},
    )
    now = datetime.now(timezone.utc)
    # Task #273: sort by symbol — defensive ordering so concurrent
    # rejection-snapshot writes for overlapping watchlists never
    # acquire FK locks (``user_id`` / ``profile_id``) in cross order.
    rows = sorted(rows, key=lambda r: r.get("symbol", ""))
    for row in rows:
        await db.execute(text("""
            INSERT INTO pipeline_watchlist_rejections (
                id, watchlist_id, user_id, profile_id, symbol, stage,
                failed_type, failed_indicator, condition_text, current_value,
                expected_value, evaluation_trace, analysis_snapshot, recorded_at, execution_id,
                engine_tag
            )
            VALUES (
                :id, :watchlist_id, :user_id, :profile_id, :symbol, :stage,
                :failed_type, :failed_indicator, :condition_text, CAST(:current_value AS jsonb),
                :expected_value, CAST(:evaluation_trace AS jsonb), CAST(:analysis_snapshot AS jsonb),
                :recorded_at, :execution_id, :engine_tag
            )
        """), {
            "id": str(uuid4()),
            "watchlist_id": watchlist_id,
            "user_id": str(user_id),
            "profile_id": str(profile_id) if profile_id else None,
            "symbol": row["symbol"],
            "stage": row["stage"],
            "failed_type": row["failed_type"],
            "failed_indicator": row["failed_indicator"],
            "condition_text": row["condition"],
            "current_value": json.dumps(_jsonable(row.get("current_value"))),
            "expected_value": row.get("expected"),
            "evaluation_trace": json.dumps(_jsonable(row.get("evaluation_trace") or [])),
            "analysis_snapshot": json.dumps(_jsonable(row.get("analysis_snapshot") or {})),
            "recorded_at": now,
            "execution_id": execution_id,
            "engine_tag": row.get("engine_tag"),
        })


# ─── Futures scoring injection ────────────────────────────────────────────────

# NOTE: The legacy ``_tag_futures_scores`` helper that wrapped
# ``app.scoring.futures_pipeline_scorer.score_futures`` was removed in
# Phase 4 — futures direction + score split are now derived directly
# from the robust envelopes inside ``_apply_robust_authoritative_scoring``.


# ─── DB upsert ────────────────────────────────────────────────────────────────

async def _upsert_assets(
    db,
    watchlist_id: str,
    assets: list,
    filters_json: dict | None = None,
    execution_id: Optional[str] = None,
):
    """Upsert current pipeline_watchlist_assets snapshot for a watchlist.

    Symbols in `assets` → INSERT or UPDATE (level_direction stays/becomes NULL).
    Symbols previously saved but not in `assets` → UPDATE level_direction = 'down'.
    Records with level_direction = 'down' older than 2h are cleaned up.
    If filters_json contains max_stay_minutes, assets older than that are expired.
    Staleness expiry: assets not refreshed in staleness_minutes (default 30) are marked 'down'.
    """
    from sqlalchemy import text

    now = datetime.now(timezone.utc)

    if assets:
        # Task #273: sort by symbol before per-row UPSERT — multiple
        # watchlists may share symbols and run concurrent scans, so
        # iterating in non-deterministic order would risk deadlocks
        # on the ``(watchlist_id, symbol)`` unique index. Same root
        # cause as #251.
        assets_sorted = sorted(assets, key=lambda a: a.get("symbol", ""))
        # Upsert active symbols (preserve entered_at on conflict, update refreshed_at)
        for a in assets_sorted:
            await db.execute(text("""
                INSERT INTO pipeline_watchlist_assets
                    (id, watchlist_id, symbol, current_price, price_change_24h,
                      volume_24h, market_cap, alpha_score, entered_at, refreshed_at,
                      level_direction, analysis_snapshot, execution_id,
                      score_long, score_short, confidence_score,
                      futures_direction, entry_long_blocked, entry_short_blocked,
                      engine_tag)
                VALUES
                    (gen_random_uuid(), :wid, :sym, :price, :chg,
                     :vol, :mc, :score, :now, :now, NULL, CAST(:analysis_snapshot AS jsonb), :execution_id,
                     :score_long, :score_short, :confidence_score,
                     :futures_direction, :entry_long_blocked, :entry_short_blocked,
                     :engine_tag)
                ON CONFLICT (watchlist_id, symbol)
                DO UPDATE SET
                    current_price       = EXCLUDED.current_price,
                    price_change_24h    = EXCLUDED.price_change_24h,
                    volume_24h          = EXCLUDED.volume_24h,
                    market_cap          = EXCLUDED.market_cap,
                    alpha_score         = EXCLUDED.alpha_score,
                    refreshed_at        = EXCLUDED.refreshed_at,
                    level_direction     = NULL,
                    analysis_snapshot   = EXCLUDED.analysis_snapshot,
                    execution_id        = EXCLUDED.execution_id,
                    score_long          = EXCLUDED.score_long,
                    score_short         = EXCLUDED.score_short,
                    confidence_score    = EXCLUDED.confidence_score,
                    futures_direction   = EXCLUDED.futures_direction,
                    entry_long_blocked  = EXCLUDED.entry_long_blocked,
                    entry_short_blocked = EXCLUDED.entry_short_blocked,
                    engine_tag          = EXCLUDED.engine_tag
            """), {
                "wid":                watchlist_id,
                "sym":                a["symbol"],
                "price":              a.get("price"),
                "chg":                a.get("change_24h"),
                "vol":                a.get("volume_24h"),
                "mc":                 a.get("market_cap"),
                "score":              a.get("_score", a.get("score")),
                "analysis_snapshot":  json.dumps(_jsonable(a.get("analysis_snapshot") or {})),
                "now":                now,
                "execution_id":       execution_id,
                "score_long":         a.get("score_long"),
                "score_short":        a.get("score_short"),
                "confidence_score":   a.get("confidence_score"),
                "futures_direction":  a.get("futures_direction"),
                "entry_long_blocked": bool(a.get("entry_long_blocked", False)),
                "entry_short_blocked": bool(a.get("entry_short_blocked", False)),
                "engine_tag":         a.get("engine_tag"),
            })

        # Mark symbols that are no longer passing as 'down'
        active_syms = [a["symbol"] for a in assets]
        await db.execute(
            text("""
                UPDATE pipeline_watchlist_assets
                SET level_direction = 'down',
                    level_change_at = :now,
                    execution_id = :execution_id
                WHERE watchlist_id = :wid
                  AND NOT (symbol = ANY(:active_syms))
                  AND (level_direction IS NULL OR level_direction != 'down')
            """),
            {"wid": watchlist_id, "now": now, "active_syms": active_syms, "execution_id": execution_id},
        )

    else:
        # No assets passed — mark all as 'down'
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now,
                execution_id = :execution_id
            WHERE watchlist_id = :wid
              AND (level_direction IS NULL OR level_direction != 'down')
        """), {"wid": watchlist_id, "now": now, "execution_id": execution_id})

    # Expire assets that have exceeded max_stay_minutes (GUI-configurable per watchlist)
    max_stay = (filters_json or {}).get("max_stay_minutes")
    if max_stay:
        cutoff = now - timedelta(minutes=int(max_stay))
        await db.execute(text("""
            UPDATE pipeline_watchlist_assets
            SET level_direction = 'down',
                level_change_at = :now,
                execution_id = :execution_id
            WHERE watchlist_id = :wid
              AND level_direction IS NULL
              AND entered_at < :cutoff
        """), {"wid": watchlist_id, "now": now, "cutoff": cutoff, "execution_id": execution_id})

    # Staleness expiry: assets not re-confirmed by a pipeline scan within
    # staleness_minutes (default 30 min) are marked 'down'.
    # This prevents assets from lingering when the scan skips due to
    # missing market data or upstream failures.
    staleness_minutes = int((filters_json or {}).get("staleness_minutes", _DEFAULT_STALENESS_MINUTES))
    staleness_cutoff = now - timedelta(minutes=staleness_minutes)
    stale_result = await db.execute(text("""
        UPDATE pipeline_watchlist_assets
        SET level_direction = 'down',
            level_change_at = :now,
            execution_id = :execution_id
        WHERE watchlist_id = :wid
          AND level_direction IS NULL
          AND refreshed_at IS NOT NULL
          AND refreshed_at < :cutoff
        RETURNING symbol
    """), {"wid": watchlist_id, "now": now, "cutoff": staleness_cutoff, "execution_id": execution_id})
    stale_rows = stale_result.fetchall()
    if stale_rows:
        logger.info(
            "[PipelineScan] Staleness expiry (%d min): marked %d assets as 'down' in watchlist %s: %s",
            staleness_minutes, len(stale_rows), watchlist_id,
            [r.symbol for r in stale_rows],
        )

    # Cleanup: remove 'down' records older than 2h to keep the table lean
    await db.execute(text("""
        DELETE FROM pipeline_watchlist_assets
        WHERE watchlist_id = :wid
          AND level_direction = 'down'
          AND level_change_at < now() - interval '2 hours'
    """), {"wid": watchlist_id})

    await db.commit()


async def validate_pipeline_integrity(
    db,
    *,
    wl_rows: list,
    profile_config_map: dict,
    execution_id: str,
) -> dict[str, Any]:
    from sqlalchemy import text

    if not wl_rows:
        return {"violations": 0, "corrected": 0}

    watchlist_ids = [str(wl.id) for wl in wl_rows]
    active_rows = (await db.execute(
        text("""
            SELECT watchlist_id::text AS watchlist_id, symbol
            FROM pipeline_watchlist_assets
            WHERE watchlist_id::text = ANY(:watchlist_ids)
              AND (level_direction IS NULL OR level_direction = 'up')
        """),
        {"watchlist_ids": watchlist_ids},
    )).fetchall()

    symbols_by_watchlist: dict[str, set[str]] = {wid: set() for wid in watchlist_ids}
    for row in active_rows:
        symbols_by_watchlist.setdefault(row.watchlist_id, set()).add(row.symbol)

    wl_map = {str(wl.id): wl for wl in wl_rows}
    violations = 0
    corrected = 0
    for wl_id, wl in wl_map.items():
        wl_level = effective_pipeline_level(
            wl.level,
            source_pool_id=wl.source_pool_id,
            profile_config=profile_config_map.get(wl.profile_id),
        )
        if wl_level not in {"L1", "L2", "L3"}:
            continue

        parent_id = str(wl.source_watchlist_id) if wl.source_watchlist_id else None
        if not parent_id:
            continue
        parent_symbols = symbols_by_watchlist.get(parent_id, set())
        child_symbols = symbols_by_watchlist.get(wl_id, set())
        invalid_symbols = sorted(child_symbols - parent_symbols)
        if not invalid_symbols:
            continue

        for symbol in invalid_symbols:
            _log_pipeline_event(
                level=wl_level,
                execution_id=execution_id,
                event_type="PIPELINE_VIOLATION",
                watchlist_id=wl_id,
                symbol=symbol,
                reason="integrity_check_not_in_upstream",
            )
        violations += len(invalid_symbols)

        await db.execute(
            text("""
                UPDATE pipeline_watchlist_assets
                SET level_direction = 'down',
                    level_change_at = :now,
                    execution_id = :execution_id
                WHERE watchlist_id::text = :watchlist_id
                  AND symbol = ANY(:symbols)
                  AND (level_direction IS NULL OR level_direction = 'up')
            """),
            {
                "now": datetime.now(timezone.utc),
                "execution_id": execution_id,
                "watchlist_id": wl_id,
                "symbols": invalid_symbols,
            },
        )
        corrected += len(invalid_symbols)

    await db.commit()
    return {"violations": violations, "corrected": corrected}


# ─── WebSocket broadcast ──────────────────────────────────────────────────────

async def _broadcast_pipeline_update(
    watchlist_id: str,
    watchlist_name: str,
    level: str,
    new_symbols: list,
    all_signals: list,
):
    """Broadcast new L3 signals via the 'signals' WebSocket channel."""
    try:
        from ..api.websocket import manager
        from datetime import datetime, timezone

        payload = {
            "type":           "pipeline_signal",
            "level":          level,
            "watchlist_id":   watchlist_id,
            "watchlist_name": watchlist_name,
            "new_signals":    new_symbols,
            "all_signals":    all_signals[:20],  # cap at 20 for WS payload
            "ts":             datetime.now(timezone.utc).isoformat(),
        }

        await manager.broadcast("signals", payload)
        logger.info(
            "[PipelineScan] Broadcast %d new L3 signals for watchlist %s",
            len(new_symbols), watchlist_name,
        )
    except Exception as exc:
        logger.warning("[PipelineScan] WebSocket broadcast failed: %s", exc)


async def _broadcast_scan_funnel(
    watchlist_id: str,
    watchlist_name: str,
    level: str,
    pool_total: int,
    with_metadata: int,
    profile_candidates: int,
    after_profile_filter: int,
    after_blocking: int,
):
    """Broadcast scan funnel stats via 'pipeline' WebSocket channel for frontend diagnostics."""
    try:
        from ..api.websocket import manager

        payload = {
            "type":           "scan_funnel",
            "level":          level,
            "watchlist_id":   watchlist_id,
            "watchlist_name": watchlist_name,
            "funnel": {
                "pool_total":            pool_total,
                "with_metadata":         with_metadata,
                "no_metadata":           pool_total - with_metadata,
                "profile_candidates":    profile_candidates,
                "after_profile_filter":  after_profile_filter,
                "rejected_by_profile":   max(0, profile_candidates - after_profile_filter),
                "after_blocking":        after_blocking,
                "blocked":               after_profile_filter - after_blocking,
            },
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        await manager.broadcast("pipeline", payload)
    except Exception as exc:
        logger.debug("[PipelineScan] Funnel broadcast failed: %s", exc)

    # Task #232 — publish funnel observations to Prometheus so the
    # dashboards can chart universe → throughput → rejection rate
    # per stage without scraping log lines.
    try:
        from ..services.execution_gate_metrics import record_pipeline_stage
        record_pipeline_stage("pool", "metadata", pool_total, with_metadata)
        record_pipeline_stage("metadata", "profile_filter",
                              profile_candidates, after_profile_filter)
        record_pipeline_stage("profile_filter", "blocking",
                              after_profile_filter, after_blocking)
    except Exception as exc:
        logger.debug("[PipelineScan] funnel metrics failed: %s", exc)


# ─── core async pipeline ──────────────────────────────────────────────────────

async def _run_pipeline_scan():
    from ..database import CeleryAsyncSessionLocal as AsyncSessionLocal
    from ..models.pipeline_watchlist import PipelineWatchlist
    from ..models.pool import PoolCoin
    from ..models.profile import Profile
    from sqlalchemy import select, text
    from ..utils.symbol_filters import filter_real_assets

    redis = _get_redis()
    execution_id = str(uuid4())
    stats = {"watchlists": 0, "new_signals": 0, "errors": 0, "funnels": [], "execution_id": execution_id}

    async with AsyncSessionLocal() as db:
        # Task #232 — orphan cleanup. ``pipeline_watchlist_assets`` rows
        # whose backing ``pool_coins`` entry was deleted between two
        # scans would otherwise stay forever (the upsert path only
        # touches symbols it sees in the current cycle). One bounded
        # DELETE per scan keeps the watchlist faithful to the pool.
        try:
            # Task #232 — orphan = no row in the *ingestion-active*
            # universe. A row that exists in pool_coins but has been
            # toggled to is_active=false is no longer being ingested
            # / scored, so its watchlist asset entry is just as stale
            # as a fully deleted row.
            orphan_res = await db.execute(text("""
                DELETE FROM pipeline_watchlist_assets pwa
                 WHERE NOT EXISTS (
                       SELECT 1 FROM pool_coins pc
                        WHERE pc.symbol    = pwa.symbol
                          AND pc.is_active = true
                 )
                RETURNING pwa.symbol
            """))
            orphan_rows = orphan_res.fetchall()
            if orphan_rows:
                from ..services.execution_gate_metrics import record_orphans_cleaned
                record_orphans_cleaned(len(orphan_rows))
                await db.commit()
                logger.info(
                    "[PipelineScan] Cleaned %d orphan watchlist asset(s) "
                    "(symbol no longer in pool_coins): %s",
                    len(orphan_rows),
                    sorted({r.symbol for r in orphan_rows})[:20],
                )
            else:
                # Roll back the empty DELETE so we do not hold a
                # write lock on the table while the scan loop runs.
                await db.rollback()
        except Exception as exc:
            logger.warning("[PipelineScan] orphan cleanup failed: %s", exc)
            try:
                await db.rollback()
            except Exception:
                pass

        # Load all pipeline watchlists with auto_refresh=true
        wl_rows = (await db.execute(
            select(PipelineWatchlist).where(PipelineWatchlist.auto_refresh == True)
        )).scalars().all()

        if not wl_rows:
            logger.debug("[PipelineScan] No pipeline watchlists with auto_refresh — skipping.")
            return stats

        # Materialise watchlists into primitive snapshots BEFORE any further
        # work. The session is guaranteed healthy here (we just loaded the
        # rows). After this point, the per-watchlist loop and integrity check
        # operate exclusively on these snapshots and never touch ORM
        # attributes — so a rollback in any iteration cannot expire fields
        # that subsequent iterations need, which eliminates the
        # MissingGreenlet lazy-load on aborted sessions and the resulting
        # InFailedSQLTransactionError cascade (Task #114).
        wl_snapshots = [
            SimpleNamespace(
                id=wl.id,
                name=wl.name,
                level=wl.level,
                market_mode=wl.market_mode,
                profile_id=wl.profile_id,
                source_pool_id=wl.source_pool_id,
                source_watchlist_id=wl.source_watchlist_id,
                user_id=wl.user_id,
                filters_json=wl.filters_json,
                created_at=wl.created_at,
            )
            for wl in wl_rows
        ]

        profile_ids = {wl.profile_id for wl in wl_snapshots if wl.profile_id}
        profile_config_map = {}
        profile_meta_map: dict = {}
        if profile_ids:
            profile_rows = (await db.execute(
                select(Profile).where(Profile.id.in_(profile_ids))
            )).scalars().all()
            profile_config_map = {row.id: row.config for row in profile_rows}
            profile_meta_map   = {
                row.id: {
                    "name":    row.name,
                    "version": getattr(row, "profile_version", None),
                }
                for row in profile_rows
            }

        wl_snapshots.sort(
            key=lambda wl: (
                WATCHLIST_STAGE_ORDER.get(
                    effective_pipeline_level(
                        wl.level,
                        source_pool_id=wl.source_pool_id,
                        profile_config=profile_config_map.get(wl.profile_id),
                    ),
                    len(WATCHLIST_STAGE_ORDER),
                ),
                wl.created_at or datetime.min.replace(tzinfo=timezone.utc),
            )
        )

        logger.info(
            "[PipelineScan] Processing %d pipeline watchlists… execution_id=%s",
            len(wl_snapshots),
            execution_id,
        )

        stage_buckets: dict[str, list] = {stage: [] for stage in (*_PIPELINE_EXECUTION_ORDER, "custom")}
        effective_level_map: dict[str, str] = {}
        pool_gate_watchlist_map: dict[tuple[str, str], str] = {}
        for _wl in wl_snapshots:
            _eff = effective_pipeline_level(
                _wl.level,
                source_pool_id=_wl.source_pool_id,
                profile_config=profile_config_map.get(_wl.profile_id),
            )
            effective_level_map[str(_wl.id)] = _eff
            stage_buckets.setdefault(_eff, []).append(_wl)
            if _eff == "POOL" and _wl.source_pool_id:
                pool_gate_watchlist_map[(str(_wl.user_id), str(_wl.source_pool_id))] = str(_wl.id)

        for stage in (*_PIPELINE_EXECUTION_ORDER, "custom"):
            for wl in stage_buckets.get(stage, []):
                # `wl` is a SimpleNamespace primitive snapshot — never an ORM
                # object — so attribute access here cannot trigger lazy-load
                # IO and cannot raise MissingGreenlet (Task #114).
                wl_id = str(wl.id)
                try:
                    stats["watchlists"] += 1
                    level = (wl.level or "L1").upper()
                    profile_config = profile_config_map.get(wl.profile_id) if wl.profile_id else None
                    effective_level = effective_pipeline_level(
                        level,
                        source_pool_id=wl.source_pool_id,
                        profile_config=profile_config,
                    )
                    filters_json = wl.filters_json or {}

                    source_watchlist_level = (
                        effective_level_map.get(str(wl.source_watchlist_id))
                        if wl.source_watchlist_id
                        else None
                    )
                    pool_gate_watchlist_id = None
                    if effective_level == "L1" and wl.source_pool_id:
                        pool_gate_watchlist_id = pool_gate_watchlist_map.get(
                            (str(wl.user_id), str(wl.source_pool_id))
                        )
                        if pool_gate_watchlist_id == wl_id:
                            pool_gate_watchlist_id = None
                    dependency = resolve_pipeline_dependency(
                        level=effective_level,
                        source_pool_id=wl.source_pool_id,
                        source_watchlist_id=wl.source_watchlist_id,
                        source_watchlist_level=source_watchlist_level,
                        pool_gate_watchlist_id=pool_gate_watchlist_id,
                    )
                    source_pool_id = dependency["source_pool_id"]
                    source_watchlist_id = dependency["source_watchlist_id"]
                    if dependency["error"]:
                        logger.error(
                            {
                                "type": "INVALID_SOURCE_CONFIG",
                                "watchlist_id": wl_id,
                                "level": effective_level,
                                "execution_id": execution_id,
                                "message": "Missing explicit upstream dependency for pipeline stage.",
                                "expected_upstream_level": dependency["expected_upstream_level"],
                                "source_pool_id": str(wl.source_pool_id) if wl.source_pool_id else None,
                                "source_watchlist_id": str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                "source_watchlist_level": source_watchlist_level,
                                "error": dependency["error"],
                            }
                        )
                    elif dependency["resolution"] == "implicit_pool_gate":
                        logger.info(
                            "[PipelineScan] %s (%s): resolved legacy POOL dependency via watchlist %s",
                            wl.name,
                            effective_level,
                            source_watchlist_id,
                        )

                    def _normalize_sym(s: str) -> str:
                        s = s.upper().strip()
                        if "_" not in s and s.endswith("USDT"):
                            return s[:-4] + "_USDT"
                        return s

                    symbols: list[str] = []
                    upstream_symbols: set[str] = set()

                    if source_watchlist_id:
                        upstream_rows = (await db.execute(text("""
                            SELECT symbol
                            FROM pipeline_watchlist_assets
                            WHERE watchlist_id = :wid
                              AND (level_direction IS NULL OR level_direction = 'up')
                            ORDER BY alpha_score DESC NULLS LAST
                        """), {"wid": source_watchlist_id})).fetchall()
                        symbols = filter_real_assets([_normalize_sym(r.symbol) for r in upstream_rows])
                        upstream_symbols = set(symbols)
                        logger.info(
                            "[PipelineScan] %s (%s): upstream watchlist %s → %d symbols",
                            wl.name, effective_level, source_watchlist_id, len(symbols),
                        )
                    elif source_pool_id:
                        wl_market_mode = wl.market_mode or "spot"
                        if not wl.market_mode:
                            logger.warning(
                                "[PipelineScan] %s: market_mode is unset — defaulting to 'spot'. "
                                "Set market_mode explicitly on the watchlist to avoid this fallback.",
                                wl.name,
                            )
                        # Task #232: pipeline funnel entry uses the
                        # ingestion gate only. Execution authorisation
                        # (``is_tradable``) is enforced downstream.
                        coin_rows = (await db.execute(
                            select(PoolCoin).where(
                                PoolCoin.pool_id == source_pool_id,
                                PoolCoin.is_active == True,
                                PoolCoin.market_type == wl_market_mode,
                            )
                        )).scalars().all()
                        symbols = filter_real_assets([_normalize_sym(c.symbol) for c in coin_rows])
                        # BLOCO A — hard structural filter (new_arch_capture_enabled)
                        # Gated flag: when False leaves behavior IDENTICAL to current.
                        # Only STRUCTURAL criteria (volume/spread/depth) — never RSI/ADX/score.
                        try:
                            from ..services.config_service import config_service
                            from ..services.pool_service import apply_structural_pool_filter
                            async with db.begin_nested():
                                _pool_cfg = await config_service.get_config(
                                    db, "pool_config", wl.user_id
                                )
                            if _pool_cfg.get("new_arch_capture_enabled", False):
                                symbols = await apply_structural_pool_filter(
                                    symbols, db, _pool_cfg
                                )
                        except Exception as _sf_exc:
                            logger.warning(
                                "[PipelineScan] %s: structural pool filter skipped (%s)",
                                wl.name, _sf_exc,
                            )
                        upstream_symbols = set(symbols)
                        logger.info(
                            "[PipelineScan] %s (%s): pool %s → %d symbols",
                            wl.name, effective_level, source_pool_id, len(symbols),
                        )

                    if effective_level in {"L1", "L2", "L3"}:
                        symbols = _intersect_with_upstream(
                            symbols=symbols,
                            upstream_symbols=upstream_symbols,
                            level=effective_level,
                            watchlist_id=wl_id,
                            execution_id=execution_id,
                        )
                        assert set(symbols).issubset(upstream_symbols)

                    if not symbols:
                        if source_watchlist_id:
                            # Upstream watchlist was consulted and approved 0 symbols.
                            # Immediately clear all active assets in this stage so the
                            # downstream reflects the upstream's 0-approved state.
                            logger.info(
                                "[PipelineScan] %s (%s): upstream watchlist %s approved 0 symbols — clearing active assets.",
                                wl.name, effective_level, source_watchlist_id,
                            )
                            await _upsert_assets(db, wl_id, [], filters_json, execution_id=execution_id)
                        else:
                            logger.info(
                                "[PipelineScan] %s (%s): no symbols from upstream — running staleness check.",
                                wl.name, effective_level,
                            )
                            await _run_staleness_only(db, wl_id, filters_json, execution_id=execution_id)
                        await _update_last_scanned(db, wl_id)
                        continue

                    assets = await _fetch_market_data(db, symbols)
                    if assets is None or not assets:
                        logger.warning(
                            "[PipelineScan] %s (%s): no market data available — running staleness check.",
                            wl.name, effective_level,
                        )
                        await _run_staleness_only(db, wl_id, filters_json, execution_id=execution_id)
                        await _update_last_scanned(db, wl_id)
                        continue

                    # Mandatory core-indicator completeness guard.
                    # Assets with null ADX, RSI, or MACD are quarantined here and
                    # never allowed to advance to any pipeline stage (POOL → L3).
                    assets, quarantined = _filter_incomplete_indicators(assets)
                    if quarantined and effective_level in {"POOL", "L1", "L2", "L3"}:
                        logger.info(
                            "[PipelineScan] %s (%s): %d asset(s) quarantined for null core indicators "
                            "and excluded from this scan cycle.",
                            wl.name, effective_level, len(quarantined),
                        )

                    assets_with_metadata = sum(1 for a in assets if a.get("_has_market_metadata"))
                    profile_candidate_count = len(assets)

                    score_config: Optional[dict] = None
                    # Best-effort score config read.  Wrapped in a SAVEPOINT so a
                    # DB-level failure (e.g. timeout, missing column) only rolls
                    # back the savepoint and leaves the parent session healthy
                    # for the rest of this watchlist's writes.  Without this,
                    # asyncpg's poisoned-tx state cascades to _upsert_assets
                    # below and ultimately to validate_pipeline_integrity at the
                    # end of the cycle (Task #125).
                    from ..services.seed_service import DEFAULT_SCORE
                    try:
                        from ..services.config_service import config_service
                        async with db.begin_nested():
                            score_config = await config_service.get_config(db, "score", wl.user_id)
                        if not score_config:
                            score_config = DEFAULT_SCORE
                    except Exception as _sc_exc:
                        logger.warning(
                            "[PipelineScan] %s: score config read failed (%s) — falling back to DEFAULT_SCORE",
                            wl.name, _sc_exc,
                        )
                        score_config = DEFAULT_SCORE

                    # Load block_config (block_rules, entry_triggers) from config_profiles.
                    # Connects Autopilot Caminho B write path to the pipeline read path (L-02, L-03 fix).
                    # Merges on top of profiles.config so autopilot-managed values take precedence.
                    if profile_config:
                        try:
                            from ..services.config_service import config_service as _block_cs
                            async with db.begin_nested():
                                _block_cfg = await _block_cs.get_config(db, "block", wl.user_id)
                            if _block_cfg:
                                _overrides = {}
                                _br = _block_cfg.get("block_rules")
                                _et = _block_cfg.get("entry_triggers")
                                if _br is not None:
                                    _overrides["block_rules"] = _br
                                if _et is not None:
                                    _overrides["entry_triggers"] = _et
                                if _overrides:
                                    profile_config = {**profile_config, **_overrides}
                        except Exception as _bc_exc:
                            logger.warning(
                                "[PipelineScan] %s: block config read failed (%s) — using profile.config block_rules",
                                wl.name, _bc_exc,
                            )

                    is_futures = getattr(wl, "market_mode", "spot") == "futures"

                    # ── Robust authoritative scoring ─────────────────────
                    # POOL-level watchlists have no score config in their
                    # profile and must never store score data — leaking score
                    # into POOL assets contaminates the ML pipeline (assets
                    # flow POOL → L1 → shadow trades, and score values
                    # bleeding into that path create data-leakage in the
                    # training set). Skip scoring entirely for POOL and
                    # explicitly remove any score that _build_pipeline_asset
                    # may have populated from the alpha_scores table.
                    # L1/L2 watchlists that belong to a score-free pool chain
                    # can opt out via filters_json.no_score = true — the same
                    # cleanup is applied so no score leaks into the DB row.
                    _no_score = (
                        effective_level == "POOL"
                        or bool((filters_json or {}).get("no_score"))
                    )
                    if _no_score:
                        for asset in assets:
                            asset.pop("score", None)
                            asset.pop("_score", None)
                            asset.pop("alpha_score", None)
                            asset.pop("score_long", None)
                            asset.pop("score_short", None)
                            asset.pop("confidence_score", None)
                            asset.pop("futures_direction", None)
                            asset.pop("engine_tag", None)
                    else:
                        # The robust deterministic score becomes the
                        # authoritative value on the asset dict; downstream
                        # rejection / upsert / UI all read from the mutated
                        # dict. For futures the LONG / SHORT split + direction
                        # tag are derived from the robust direction bias —
                        # the legacy ``futures_pipeline_scorer`` is no longer
                        # invoked. When the robust step itself raises we
                        # fail-closed: every asset score is zeroed and the
                        # row is tagged ``robust`` so a pre-existing legacy
                        # number is never persisted under an audited tag.
                        try:
                            rollout_counters = await _apply_robust_authoritative_scoring(
                                assets,
                                score_config=score_config,
                                is_futures=is_futures,
                                db=db,
                                user_id=getattr(wl, "user_id", None),
                                watchlist_id=wl_id,
                            )
                            if rollout_counters["bucketed"] or rollout_counters["fallbacks"]:
                                logger.info(
                                    "[PipelineScan] %s (%s): robust scoring — bucketed=%d "
                                    "robust_used=%d fallbacks=%d",
                                    wl.name, effective_level,
                                    rollout_counters["bucketed"],
                                    rollout_counters["robust_used"],
                                    rollout_counters["fallbacks"],
                                )
                        except Exception as _rollout_exc:
                            logger.error(
                                "[PipelineScan] %s (%s): robust scoring step failed "
                                "(%s) — failing CLOSED (zeroing scores)",
                                wl.name, effective_level, _rollout_exc,
                            )
                            for asset in assets:
                                asset["engine_tag"] = "robust"
                                asset["_score"] = 0.0
                                asset["score"] = 0.0
                                asset["alpha_score"] = 0.0
                                if is_futures:
                                    asset["confidence_score"] = 0.0
                                    if asset.get("score_long") is not None:
                                        asset["score_long"] = 0.0
                                    if asset.get("score_short") is not None:
                                        asset["score_short"] = 0.0

                    if effective_level == "custom":
                        existing_symbols = {a.get("symbol") for a in assets}
                        missing_symbols = [sym for sym in symbols if sym not in existing_symbols]
                        if missing_symbols:
                            assets.extend([_placeholder_asset_without_market_data(sym) for sym in missing_symbols])
                        monitored, _ = _apply_level_filter(
                            assets,
                            profile_config,
                            effective_level,
                            score_config=score_config,
                            apply_profile_filters=False,
                        )
                        # Minimum score gate.
                        # PRIMARY: score_config.minimum_score (managed by Auto-Pilot via config_profiles).
                        # DEPRECATED FALLBACK: filters_json.min_alpha_score (watchlist-level override).
                        _autopilot_min = (score_config or {}).get("minimum_score")
                        _legacy_min = float((filters_json or {}).get("min_alpha_score") or 0)
                        if _legacy_min > 0 and _autopilot_min is None:
                            logger.warning(
                                "[PipelineScan] %s: min_alpha_score via filters_json is DEPRECATED — "
                                "migrate to config_profiles(score).minimum_score for autopilot management",
                                wl.name,
                            )
                        _wl_min_score = float(_autopilot_min if _autopilot_min is not None else _legacy_min)
                        if _wl_min_score > 0:
                            _pre = len(monitored)
                            monitored = [
                                a for a in monitored
                                if (a.get("_score") or a.get("alpha_score") or 0) >= _wl_min_score
                            ]
                            if len(monitored) < _pre:
                                logger.info(
                                    "[PipelineScan] custom min_alpha_score gate (%.1f): %d → %d approved",
                                    _wl_min_score, _pre, len(monitored),
                                )
                        await _replace_rejection_snapshot(
                            db, wl_id, wl.user_id, wl.profile_id, [], execution_id=execution_id
                        )
                        await _upsert_assets(db, wl_id, monitored, filters_json, execution_id=execution_id)
                        await _update_last_scanned(db, wl_id)
                        continue

                    if effective_level in ("POOL", "L1", "L2"):
                        effective_profile_config = profile_config
                        selected_filter_conditions = None
                        if profile_config:
                            filter_cfg = (profile_config.get("filters") or {})
                            selected = select_profile_filter_conditions(
                                filter_cfg.get("conditions"),
                                total_symbols=len(symbols),
                                symbols_with_meta=assets_with_metadata,
                            )
                            selected_filter_conditions = selected["conditions"]
                            if selected["relaxed_strict_meta"]:
                                effective_profile_config = {
                                    **profile_config,
                                    "filters": {**filter_cfg, "conditions": selected["conditions"]},
                                }

                        profile_passed, rejected_rows = evaluate_rejections(
                            assets,
                            profile_config=effective_profile_config,
                            stage=effective_level,
                            profile_id=str(wl.profile_id) if wl.profile_id else None,
                            selected_filter_conditions=selected_filter_conditions,
                        )
                        _log_stage_processing_summary(
                            level=effective_level,
                            input_count=len(symbols),
                            approved_count=len(profile_passed),
                            rejected_count=len(rejected_rows),
                            watchlist_id=wl_id,
                            execution_id=execution_id,
                        )
                        passed, _ = _apply_level_filter(
                            profile_passed,
                            effective_profile_config,
                            effective_level,
                            score_config=score_config,
                            apply_profile_filters=False,
                        )
                        await _replace_rejection_snapshot(
                            db,
                            wl_id,
                            wl.user_id,
                            wl.profile_id,
                            rejected_rows,
                            execution_id=execution_id,
                        )

                        if effective_level in {"L1", "L2"}:
                            normalized_passed = []
                            for asset in passed:
                                symbol = asset.get("symbol")
                                if symbol in upstream_symbols:
                                    normalized_passed.append(asset)
                                else:
                                    _log_pipeline_event(
                                        level=effective_level,
                                        execution_id=execution_id,
                                        event_type="PIPELINE_VIOLATION",
                                        watchlist_id=wl_id,
                                        symbol=symbol,
                                        reason="persist_not_in_upstream",
                                    )
                            passed = normalized_passed
                            assert {a.get("symbol") for a in passed}.issubset(upstream_symbols)

                        await _broadcast_scan_funnel(
                            wl_id, wl.name, effective_level,
                            pool_total=len(symbols),
                            with_metadata=assets_with_metadata,
                            profile_candidates=profile_candidate_count,
                            after_profile_filter=len(profile_passed),
                            after_blocking=len(passed),
                        )
                        await _upsert_assets(db, wl_id, passed, filters_json, execution_id=execution_id)
                        await _update_last_scanned(db, wl_id)

                        # L1_SPECTRUM capture — after upsert, before continue.
                        # Pureza invariant: no quality conditionals between here
                        # and shadow creation (only structural: sampling + reentry).
                        if effective_level == "L1":
                            try:
                                from ..services.shadow_trade_service import (
                                    create_l1_spectrum_shadows,
                                )
                                _l1_profile_meta = (
                                    profile_meta_map.get(wl.profile_id)
                                    if wl.profile_id else {}
                                ) or {}
                                await create_l1_spectrum_shadows(
                                    user_id=wl.user_id,
                                    symbols=[a["symbol"] for a in passed],
                                    execution_id=str(execution_id),
                                    assets_by_symbol={a["symbol"]: a for a in passed},
                                    promotion_at=datetime.now(timezone.utc),
                                    watchlist_id=str(wl.id),
                                    watchlist_name=wl.name,
                                    watchlist_level=wl.level,
                                    source_watchlist_id=str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                    profile_id=str(wl.profile_id) if wl.profile_id else None,
                                    profile_name=_l1_profile_meta.get("name"),
                                    profile_version=_l1_profile_meta.get("version"),
                                )
                            except Exception as _l1cap_exc:
                                logger.warning(
                                    "[PipelineScan] L1_SPECTRUM capture failed (%s)"
                                    " — L3 stream unaffected",
                                    _l1cap_exc,
                                )

                        continue

                    # L3
                    profile_passed, rejected_rows = evaluate_rejections(
                        assets,
                        profile_config=profile_config,
                        stage=effective_level,
                        profile_id=str(wl.profile_id) if wl.profile_id else None,
                    )
                    # Minimum score gate for L3.
                    # PRIMARY: score_config.minimum_score (managed by Auto-Pilot via config_profiles).
                    # DEPRECATED FALLBACK: filters_json.min_alpha_score (watchlist-level override).
                    _autopilot_min = (score_config or {}).get("minimum_score")
                    _legacy_min = float((filters_json or {}).get("min_alpha_score") or 0)
                    if _legacy_min > 0 and _autopilot_min is None:
                        logger.warning(
                            "[PipelineScan] %s: min_alpha_score via filters_json is DEPRECATED — "
                            "migrate to config_profiles(score).minimum_score for autopilot management",
                            wl.name,
                        )
                    _wl_min_score = float(_autopilot_min if _autopilot_min is not None else _legacy_min)
                    # Assets captured here are injected as BLOCK decisions after
                    # _evaluate_l3_decisions so the L3_REJECTED edge trigger logs them
                    # to decisions_log for ML data collection.
                    _gate_rejected: list = []
                    if _wl_min_score > 0:
                        _pre = len(profile_passed)
                        _low_score = [
                            a for a in profile_passed
                            if (a.get("_score") or a.get("alpha_score") or 0) < _wl_min_score
                        ]
                        profile_passed = [
                            a for a in profile_passed
                            if (a.get("_score") or a.get("alpha_score") or 0) >= _wl_min_score
                        ]
                        if _low_score:
                            _gate_rejected = list(_low_score)
                            logger.info(
                                "[PipelineScan] L3 min_alpha_score gate (%.1f): %d → %d passed (%d below threshold)",
                                _wl_min_score, _pre, len(profile_passed), len(_low_score),
                            )
                            # Merge low-score assets into rejected_rows so they appear in the Rejected tab.
                            for _a in _low_score:
                                rejected_rows.append({
                                    "symbol": _a.get("symbol", ""),
                                    "score": _a.get("_score") or _a.get("alpha_score") or 0,
                                    "rejection_reasons": [{"reason": f"score < min_alpha_score ({_wl_min_score:g})", "stage": "L3"}],
                                    "stage": "L3",
                                    "status": "rejected",
                                    # Required keys for _replace_rejection_snapshot (line 1725-1727).
                                    # Missing them raised KeyError 'failed_type' and aborted the
                                    # entire L3 watchlist iteration, preventing _evaluate_l3_decisions
                                    # from running and zeroing decisions_log persistence.
                                    "failed_type": "score_gate",
                                    "failed_indicator": "alpha_score",
                                    "condition": f"alpha_score >= {_wl_min_score:g}",
                                    "current_value": _a.get("_score") or _a.get("alpha_score") or 0,
                                    "expected": f">= {_wl_min_score:g}",
                                })
                    await _replace_rejection_snapshot(
                        db,
                        wl_id,
                        wl.user_id,
                        wl.profile_id,
                        rejected_rows,
                        execution_id=execution_id,
                    )
                    decisions = await _evaluate_l3_decisions(
                        profile_passed,
                        profile_config,
                        level,
                        score_config=score_config,
                        # Live order flow injection: pré-fetch do snapshot
                        # WS/Redis por candidato antes da regra L3 avaliar.
                        # Sem esses kwargs o helper degrada para o
                        # comportamento legado (lê só do DB).
                        db=db,
                        user_id=wl.user_id,
                        pool_id=wl.source_pool_id,
                    )
                    # Inject gate-rejected assets as BLOCK decisions so they flow through
                    # the L3_REJECTED edge trigger → decisions_log → shadow trade (ML data).
                    # Uses _decision_metrics with empty processed ({}) — indicators are
                    # captured from the asset; score components / signal fields are absent
                    # but that is expected for score-gate rejections.
                    if _gate_rejected:
                        _gate_timeframe = (profile_config or {}).get("default_timeframe", "5m")
                        for _ga in _gate_rejected:
                            decisions.append({
                                "symbol": _ga.get("symbol"),
                                "strategy": level,
                                "timeframe": _gate_timeframe,
                                "score": float(_ga.get("_score") or _ga.get("alpha_score") or 0),
                                "decision": "BLOCK",
                                "l1_pass": True,
                                "l2_pass": True,
                                "l3_pass": False,
                                "reasons": {
                                    "score_gate": f"score below min_alpha_score ({_wl_min_score:g})",
                                },
                                "metrics": _decision_metrics(_ga, {}),
                                "latency_ms": 0,
                                "direction": (
                                    _ga.get("futures_direction")
                                    or ("NEUTRAL" if _ga.get("is_futures") else "SPOT")
                                ),
                                "created_at": datetime.now(timezone.utc),
                            })
                    # ── ML Gate (pós-L3) ─────────────────────────────────────────────────
                    # Runs the XGBoost WIN_FAST model on every ALLOW decision and overrides
                    # to BLOCK when the model rejects the signal.
                    #
                    # Activation: env ML_GATE_ENABLED=true (default false — model needs
                    # enough labeled shadow data before gating real signals).
                    #
                    # Design contracts:
                    # * Never blocks the pipeline on failure — any exception falls through
                    #   with model_approved=True (safe default).
                    # * Stores win_fast_probability in decision["metrics"] so it flows
                    #   into decisions_log.metrics for downstream analysis.
                    # * ML-blocked decisions become BLOCK with reason "ml_gate" and are
                    #   treated as L3_REJECTED by the edge trigger below (ML data capture).
                    # * ml_predictions rows are written post-persist (after decision IDs
                    #   are known) via _ml_gate_log_predictions().
                    import os as _os
                    _ml_gate_enabled = _os.getenv("ML_GATE_ENABLED", "false").lower() == "true"
                    # BLOCO D — new_arch_l3_uses_ml_score: alternativa DB-based ao env var.
                    # Permite ativar o ML gate via pool_config sem redeploy.
                    # Gated: quando false E ML_GATE_ENABLED=false → comportamento IDÊNTICO ao atual.
                    if not _ml_gate_enabled and effective_level == "L3":
                        try:
                            from ..services.config_service import config_service
                            async with db.begin_nested():
                                _pool_cfg_l3 = await config_service.get_config(
                                    db, "pool_config", wl.user_id
                                )
                            if _pool_cfg_l3.get("new_arch_l3_uses_ml_score", False):
                                _ml_gate_enabled = True
                                logger.info(
                                    "[MLGate] new_arch_l3_uses_ml_score=true — "
                                    "ML gate activado via pool_config wl=%s",
                                    wl.name,
                                )
                        except Exception as _dblk_exc:
                            logger.debug(
                                "[MLGate] pool_config read failed (%s) — "
                                "usando ML_GATE_ENABLED env",
                                _dblk_exc,
                            )
                    # Dict[symbol → {"probability": float|None, "approved": bool}]
                    # populated here, consumed post-persist to write ml_predictions rows.
                    _ml_gate_scores: dict = {}

                    if _ml_gate_enabled:
                        _ml_allow_decisions = [
                            d for d in decisions if d.get("decision") == "ALLOW"
                        ]
                        if _ml_allow_decisions:
                            try:
                                from ..ml.prediction_service import predictor as _ml_predictor

                                # ML Opportunity Ranking producer (audit 2026-06-24,
                                # item 7 of the post-VALIDACAO_GERAL punch list).
                                # One run_id per watchlist scan cycle that reaches
                                # the ML gate — groups every symbol scored in this
                                # batch for later reconstruction of "the full
                                # ranking of that cycle".
                                _ml_run_id = uuid4()

                                async def _record_ml_opportunity_ranking(
                                    d: dict, ml_result: dict
                                ):
                                    """Insert one ML gate ranking row without poisoning the parent tx."""
                                    from sqlalchemy import text as _ranking_text
                                    try:
                                        async with db.begin_nested():
                                            _res = await db.execute(
                                            _ranking_text(
                                                """
                                                INSERT INTO ml_opportunity_rankings (
                                                    id, run_id, symbol, profile_id, watchlist_id,
                                                    model_lane, model_id, model_version,
                                                    promotion_gate_status,
                                                    win_fast_probability, score_status, reason_code,
                                                    threshold_used, gate_action, used_by_gate,
                                                    p_l1_win, rank_position, rank_percentile,
                                                    p_l3_profile_win,
                                                    l1_ranker_mode, selected_by_l1_ranker,
                                                    reason_codes, orchestrator_payload, source,
                                                    features_snapshot
                                                ) VALUES (
                                                    gen_random_uuid(), :run_id, :symbol,
                                                    CAST(:profile_id AS UUID), CAST(:watchlist_id AS UUID),
                                                    :model_lane, CAST(:model_id AS UUID), :model_version,
                                                    :promotion_gate_status,
                                                    :win_fast_probability, :score_status, :reason_code,
                                                    :threshold_used, :gate_action, TRUE,
                                                    :p_l1_win, :rank_position, :rank_percentile,
                                                    :p_l3_profile_win,
                                                    :l1_ranker_mode, :selected_by_l1_ranker,
                                                    CAST(:reason_codes AS JSONB),
                                                    CAST(:orchestrator_payload AS JSONB), :source,
                                                    CAST(:features_snapshot AS JSONB)
                                                )
                                                RETURNING id
                                                """
                                            ),
                                            {
                                                "run_id": str(_ml_run_id),
                                                "symbol": d.get("symbol"),
                                                "source": "L3_ML_GATE",
                                                "profile_id": d.get("profile_id"),
                                                "watchlist_id": str(wl.id),
                                                "model_lane": "L3_PROFILE",
                                                "model_id": ml_result.get("model_id"),
                                                "model_version": ml_result.get("model_version"),
                                                "promotion_gate_status": (
                                                    "APPROVED" if ml_result.get("model_id") else None
                                                ),
                                                "win_fast_probability": ml_result.get("win_fast_probability"),
                                                "score_status": ml_result.get("score_status") or (
                                                    "OK" if ml_result.get("model_id") else "SKIPPED"
                                                ),
                                                "reason_code": ml_result.get("reason_code"),
                                                "threshold_used": ml_result.get("threshold_used"),
                                                "gate_action": ml_result.get("effective_gate_action") or ("ALLOW" if ml_result.get("model_approved") else "BLOCK"),
                                                "p_l1_win": ml_result.get("p_l1_win"),
                                                "rank_position": ml_result.get("l1_rank_position"),
                                                "rank_percentile": ml_result.get("l1_rank_percentile"),
                                                "p_l3_profile_win": (
                                                    ml_result.get("win_fast_probability")
                                                    if ml_result.get("selected_by_l1_ranker")
                                                    else None
                                                ),
                                                "l1_ranker_mode": ml_result.get("l1_ranker_mode"),
                                                "selected_by_l1_ranker": ml_result.get("selected_by_l1_ranker"),
                                                "reason_codes": __import__("json").dumps(
                                                    [
                                                        code for code in [
                                                            *list(ml_result.get("reason_codes") or []),
                                                            ml_result.get("reason_code"),
                                                            "ML_GATE_ALLOWED" if ml_result.get("model_approved") else "ML_GATE_BLOCKED",
                                                        ]
                                                        if code
                                                    ]
                                                ),
                                                "orchestrator_payload": __import__("json").dumps({
                                                    "p_l1_win": ml_result.get("p_l1_win"),
                                                    "l1_model_id": ml_result.get("l1_model_id"),
                                                    "l1_model_version": ml_result.get("l1_model_version"),
                                                    "l1_rank_position": ml_result.get("l1_rank_position"),
                                                    "l1_rank_percentile": ml_result.get("l1_rank_percentile"),
                                                    "l1_ranker_mode": ml_result.get("l1_ranker_mode"),
                                                    "selected_by_l1_ranker": ml_result.get("selected_by_l1_ranker"),
                                                    "p_l3_profile_win": ml_result.get("win_fast_probability"),
                                                    "l3_model_id": ml_result.get("model_id"),
                                                    "l3_model_version": ml_result.get("model_version"),
                                                    "threshold_l3": ml_result.get("threshold_used"),
                                                    "score_status": ml_result.get("score_status") or (
                                                        "OK" if ml_result.get("model_id") else "SKIPPED"
                                                    ),
                                                    "gate_action": ml_result.get("effective_gate_action") or ("ALLOW" if ml_result.get("model_approved") else "BLOCK"),
                                                }),
                                                "features_snapshot": __import__("json").dumps(
                                                    ml_result.get("features_snapshot") or {}
                                                ),
                                            },
                                            )
                                            _row = _res.fetchone()
                                            return _row[0] if _row is not None else None
                                    except Exception as _rank_exc:
                                        logger.warning(
                                            "[MLOpportunityRanking] insert failed for %s: %s "
                                            "transaction_rolled_back=true watchlist_id=%s "
                                            "profile_id=%s lane=%s reason_code=%s exception_type=%s",
                                            d.get("symbol"), _rank_exc, wl.id,
                                            d.get("profile_id"), "L3_PROFILE",
                                            ml_result.get("reason_code"),
                                            type(_rank_exc).__name__,
                                        )
                                        return None

                                async def _ml_predict_one(d: dict) -> dict:
                                    try:
                                        return await _ml_predictor.predict(
                                            metrics=d.get("metrics") or {},
                                            db=db,
                                            symbol=d.get("symbol"),
                                            # decision_id not yet known — ml_predictions
                                            # row written post-persist below.
                                            decision_id=None,
                                            profile_id=d.get("profile_id"),
                                            # Audit P2-5 fix: this gate runs only
                                            # inside the L3 block (effective_level
                                            # == "L3", checked above) — the
                                            # intended lane is always L3_PROFILE.
                                            model_lane="L3_PROFILE",
                                        )
                                    except Exception as _exc:
                                        logger.warning(
                                            "[MLGate] predict failed for %s: %s",
                                            d.get("symbol"), _exc,
                                        )
                                        return {
                                            "model_approved": False,
                                            "win_fast_probability": None,
                                            "threshold_used": None,
                                            "model_id": None,
                                            "model_lane": "L3_PROFILE",
                                            "score_status": "ML_EXCEPTION_FAIL_CLOSED",
                                            "reason_code": "ML_EXCEPTION_FAIL_CLOSED",
                                            "reason": str(_exc),
                                        }

                                async def _l1_predict_one(d: dict) -> dict:
                                    try:
                                        return await _ml_predictor.predict(
                                            metrics=d.get("metrics") or {},
                                            db=db,
                                            symbol=d.get("symbol"),
                                            decision_id=None,
                                            profile_id=None,
                                            model_lane="L1_SPECTRUM",
                                        )
                                    except Exception as _exc:
                                        logger.warning(
                                            "[MLGate] L1 ranker failed for %s: %s",
                                            d.get("symbol"), _exc,
                                        )
                                        return {
                                            "model_approved": False,
                                            "win_fast_probability": None,
                                            "threshold_used": None,
                                            "model_id": None,
                                            "model_version": None,
                                            "model_lane": "L1_SPECTRUM",
                                            "score_status": "SKIPPED",
                                            "reason_code": "L1_MODEL_UNAVAILABLE",
                                            "reason": str(_exc),
                                        }

                                _l1_preds = await asyncio.gather(
                                    *[_l1_predict_one(d) for d in _ml_allow_decisions]
                                )
                                _l1_rank_by_symbol = _rank_l1_candidates(
                                    list(zip(_ml_allow_decisions, _l1_preds))
                                )
                                _ml_blocked_count = 0
                                for _d in _ml_allow_decisions:
                                    _sym = _d.get("symbol")
                                    _l1_rank = _l1_rank_by_symbol.get(_sym) or {
                                        "selected": False,
                                        "reason_code": "L1_MODEL_UNAVAILABLE",
                                        "reason_codes": ["L1_MODEL_UNAVAILABLE"],
                                        "selected_by_l1_ranker": False,
                                    }
                                    if _l1_rank.get("selected"):
                                        _ml = await _ml_predict_one(_d)
                                    else:
                                        _ml = {
                                            "model_approved": False,
                                            "win_fast_probability": _l1_rank.get("p_l1_win"),
                                            "threshold_used": _l1_rank.get("threshold_l1"),
                                            "model_id": _l1_rank.get("l1_model_id"),
                                            "model_version": _l1_rank.get("l1_model_version"),
                                            "model_lane": "L1_SPECTRUM",
                                            "score_status": (
                                                "OK" if _l1_rank.get("p_l1_win") is not None else "SKIPPED"
                                            ),
                                            "reason_code": _l1_rank.get("reason_code"),
                                        }
                                    _ml.update(_l1_rank)
                                    _prob = _ml.get("win_fast_probability")
                                    _approved = bool(_ml.get("model_approved", False))
                                    # P2 fix (Fase 1.6): rebaixa ALLOW→BLOCK só quando o gate tem
                                    # veredito real de rejeição; sem modelo (SKIPPED) passa direto.
                                    _ml_rejects = _ml_gate_should_block(_ml)
                                    _decision_after_ml = "BLOCK" if _ml_rejects else "ALLOW"
                                    _ml["effective_gate_action"] = _decision_after_ml
                                    _ranking_id = await _record_ml_opportunity_ranking(_d, _ml)
                                    _gate_payload = _ml_gate_audit_payload(
                                        _ml,
                                        decision_before_ml="ALLOW",
                                        decision_after_ml=_decision_after_ml,
                                        model_lane="L3_PROFILE",
                                    )
                                    _combined_reason_codes = list(dict.fromkeys(
                                        list(_ml.get("reason_codes") or [])
                                        + list(_gate_payload.get("reason_codes") or [])
                                    ))
                                    _gate_payload["reason_codes"] = _combined_reason_codes
                                    _ml_gate_scores[_sym] = {
                                        "probability": _prob,
                                        "approved": _approved,
                                        "threshold": _ml.get("threshold_used"),
                                        "model_id": _ml.get("model_id"),
                                        "model_version": _ml.get("model_version"),
                                        "reason_code": _gate_payload.get("reason_code"),
                                        "reason_codes": _combined_reason_codes,
                                        "score_status": _gate_payload.get("score_status"),
                                        "promotion_gate_status": (
                                            "APPROVED" if _ml.get("model_id") else None
                                        ),
                                        "gate_action": _gate_payload.get("gate_action"),
                                        "gate_payload": _gate_payload,
                                        "orchestrator_payload": {
                                            "p_l1_win": _ml.get("p_l1_win"),
                                            "l1_model_id": _ml.get("l1_model_id"),
                                            "l1_model_version": _ml.get("l1_model_version"),
                                            "l1_rank_position": _ml.get("l1_rank_position"),
                                            "l1_rank_percentile": _ml.get("l1_rank_percentile"),
                                            "l1_ranker_mode": _ml.get("l1_ranker_mode"),
                                            "selected_by_l1_ranker": _ml.get("selected_by_l1_ranker"),
                                            "p_l3_profile_win": _prob,
                                            "l3_model_id": _ml.get("model_id"),
                                            "l3_model_version": _ml.get("model_version"),
                                            "threshold_l3": _ml.get("threshold_used"),
                                            "score_status": _gate_payload.get("score_status"),
                                            "gate_action": _gate_payload.get("gate_action"),
                                            "reason_codes": _combined_reason_codes,
                                            "decision_before_ml": "ALLOW",
                                            "decision_after_ml": _decision_after_ml,
                                            "probability_valid": _prob is not None,
                                            "probability_error": _ml.get("reason") if _gate_payload.get("score_status") == "ML_EXCEPTION_FAIL_CLOSED" else None,
                                            "raw_model_output": _ml.get("raw_model_output"),
                                        },
                                        # Fase 8 lineage — this gate only runs for
                                        # effective_level == "L3", so the lane is
                                        # always L3_PROFILE (see model_lane= above).
                                        "model_lane": "L3_PROFILE",
                                        # Fase 6/7 — ML Opportunity Ranking lineage.
                                        "ranking_id": str(_ranking_id) if _ranking_id else None,
                                    }
                                    _d["ranking_id"] = _ml_gate_scores[_sym]["ranking_id"]
                                    _d["model_id"] = _ml.get("model_id")
                                    _d["model_version"] = _ml.get("model_version")
                                    _d["model_lane"] = "L3_PROFILE"
                                    _d["probability"] = _prob
                                    _d["threshold_used"] = _ml.get("threshold_used")
                                    _d["score_status"] = _gate_payload.get("score_status")
                                    _d["gate_action"] = _gate_payload.get("gate_action")
                                    _d["reason_codes"] = _combined_reason_codes
                                    _d["orchestrator_payload"] = _ml_gate_scores[_sym]["orchestrator_payload"]
                                    _d["ml_gate_enabled"] = True
                                    # Embed probability so it reaches decisions_log
                                    if isinstance(_d.get("metrics"), dict):
                                        _d["metrics"]["win_fast_probability"] = _prob
                                        _d["metrics"]["ml_threshold"] = _ml.get("threshold_used")
                                        _d["metrics"]["ml_model_id"] = _ml.get("model_id")
                                        _d["metrics"]["ml_model_type"] = "xgboost"
                                    _reasons = _d.setdefault("reasons", {})
                                    _reasons["ml_gate"] = _gate_payload["ml_gate"]
                                    _reasons["model_approved"] = _gate_payload["model_approved"]
                                    _reasons["reason_code"] = _gate_payload["reason_code"]
                                    _reasons["score_status"] = _gate_payload["score_status"]
                                    _reasons["model_lane"] = _gate_payload["model_lane"]
                                    _reasons["model_id"] = _gate_payload["model_id"]
                                    _reasons["decision_before_ml"] = _gate_payload["decision_before_ml"]
                                    _reasons["decision_after_ml"] = _gate_payload["decision_after_ml"]
                                    _reasons["fallback_used"] = _gate_payload["fallback_used"]
                                    _reasons["fallback_policy"] = _gate_payload["fallback_policy"]
                                    _reasons["ml_gate_payload"] = _gate_payload
                                    if _ml_rejects:
                                        _d["decision"] = "BLOCK"
                                        _d["l3_pass"] = False
                                        _ml_blocked_count += 1
                                if _ml_blocked_count:
                                    logger.info(
                                        "[MLGate] wl=%s: %d/%d ALLOW blocked by ML gate",
                                        wl.name, _ml_blocked_count, len(_ml_allow_decisions),
                                    )
                                else:
                                    logger.info(
                                        "[MLGate] wl=%s: all %d ALLOW passed ML gate "
                                        "(avg_prob=%.3f)",
                                        wl.name, len(_ml_allow_decisions),
                                        sum(
                                            s["probability"] for s in _ml_gate_scores.values()
                                            if s["probability"] is not None
                                        ) / max(
                                            sum(
                                                1 for s in _ml_gate_scores.values()
                                                if s["probability"] is not None
                                            ), 1
                                        ),
                                    )
                            except Exception as _ml_gate_exc:
                                logger.warning(
                                    "[MLGate] ML gate setup failed for wl=%s, "
                                    "falling through: %s",
                                    wl.name, _ml_gate_exc,
                                )

                    # ── L3_VISIBLE diagnostic (TEMP) — remove once root cause confirmed ──
                    _allow_count = sum(1 for d in decisions if d.get("decision") == "ALLOW")
                    _block_count = sum(1 for d in decisions if d.get("decision") == "BLOCK")
                    logger.info(
                        "[L3_DIAG] wl=%s decisions=%d ALLOW=%d BLOCK=%d profile_passed=%d"
                        "%s",
                        wl.name, len(decisions), _allow_count, _block_count, len(profile_passed),
                        " [ML_GATE_ON]" if _ml_gate_enabled else "",
                    )

                    # ── Opportunity Snapshots — captures every evaluated asset ──
                    try:
                        from ..models.opportunity_snapshot import OpportunitySnapshot as _OppSnap
                        _opp_rows = []
                        _opp_prof_id = wl.profile_id
                        for _od in decisions:
                            _feats = (_od.get("metrics") or {}).get("indicators_snapshot") or {}
                            _is_allow = _od.get("decision") == "ALLOW"
                            _opp_rows.append(_OppSnap(
                                user_id=wl.user_id,
                                symbol=_od["symbol"],
                                watchlist_id=wl.id,
                                execution_id=str(execution_id),
                                source="L3_GATE",
                                timeframe=_od.get("timeframe"),
                                price=(_od.get("_asset") or {}).get("price"),
                                features_json=_feats,
                                profiles_evaluated=[_opp_prof_id] if _opp_prof_id else None,
                                profiles_approved=[_opp_prof_id] if (_opp_prof_id and _is_allow) else None,
                                profiles_rejected=[_opp_prof_id] if (_opp_prof_id and not _is_allow) else None,
                                rejection_reasons={"reasons": _od.get("reasons")} if _od.get("reasons") and not _is_allow else None,
                                active_profiles_result_json={"decision": _od.get("decision"), "score": _od.get("score")},
                            ))
                        if _opp_rows:
                            db.add_all(_opp_rows)
                    except Exception as _opp_exc:
                        logger.debug("[OpportunitySnapshot] capture failed: %s", _opp_exc)

                    signals = [
                        {
                            "symbol": decision["symbol"],
                            "score": decision.get("score", 0),
                            "price": decision["_asset"].get("price", 0),
                            "change_24h": decision["_asset"].get("change_24h", 0),
                            "volume_24h": decision["_asset"].get("volume_24h"),
                            "market_cap": decision["_asset"].get("market_cap"),
                            "analysis_snapshot": decision["_asset"].get("analysis_snapshot") or {},
                            "matched_conditions": decision["_processed"].get("signal", {}).get("matched_conditions", []),
                            # Futures scores — non-None only when is_futures and the robust
                            # scorer produced a score for the symbol.
                            "score_long":          decision["_asset"].get("score_long"),
                            "score_short":         decision["_asset"].get("score_short"),
                            "confidence_score":    decision["_asset"].get("confidence_score"),
                            "futures_direction":   decision["_asset"].get("futures_direction"),
                            "entry_long_blocked":  decision["_asset"].get("entry_long_blocked", False),
                            "entry_short_blocked": decision["_asset"].get("entry_short_blocked", False),
                        }
                        for decision in decisions
                        if decision["decision"] == "ALLOW"
                    ]
                    normalized_signals = []
                    for asset in signals:
                        symbol = asset.get("symbol")
                        if symbol in upstream_symbols:
                            normalized_signals.append(asset)
                        else:
                            _log_pipeline_event(
                                level="L3",
                                execution_id=execution_id,
                                event_type="PIPELINE_VIOLATION",
                                watchlist_id=wl_id,
                                symbol=symbol,
                                reason="persist_not_in_upstream",
                            )
                    signals = normalized_signals
                    assert {a.get("symbol") for a in signals}.issubset(upstream_symbols)

                    current_set = {s["symbol"] for s in signals}
                    prior_set = _prior_signals(redis, wl_id)
                    new_syms = sorted(current_set - prior_set)

                    _save_signals(redis, wl_id, current_set)

                    # ── Decision Log deduplication ────────────────────────────
                    from ..services.seed_service import DEFAULT_DECISION_LOG as _DL_DEFAULTS
                    dl_score_delta = float(_DL_DEFAULTS.get("score_delta_threshold", 5.0))
                    dl_direction_logs = bool(_DL_DEFAULTS.get("direction_change_logs", True))
                    # Best-effort decision-log config read.  SAVEPOINT-wrapped
                    # for the same reason as the score config above (Task #125):
                    # a swallowed exception here used to poison the parent tx
                    # and cascade into the next _upsert_assets call.
                    try:
                        from ..services.config_service import config_service
                        async with db.begin_nested():
                            _dl_cfg = await config_service.get_config(db, "decision_log", wl.user_id)
                        if isinstance(_dl_cfg, dict):
                            dl_score_delta = float(_dl_cfg.get("score_delta_threshold", dl_score_delta))
                            dl_direction_logs = bool(_dl_cfg.get("direction_change_logs", dl_direction_logs))
                    except Exception as _dl_cfg_exc:
                        logger.warning(
                            "[PipelineScan] %s: decision_log config read failed (%s) — using defaults",
                            wl.name, _dl_cfg_exc,
                        )

                    # Profile attribution for this watchlist's decisions_log rows
                    _wl_prof_meta = profile_meta_map.get(wl.profile_id) if wl.profile_id else {}
                    _wl_profile_name    = (_wl_prof_meta or {}).get("name")
                    _wl_profile_version = (_wl_prof_meta or {}).get("version")

                    prior_states = _prior_decision_states(redis, wl_id)
                    prior_visibility = _prior_l3_visibility(redis, wl_id)
                    new_states: dict = {}
                    current_l3_visibility: set = set()
                    decisions_to_log: list = []
                    # Task #310: deterministic symbol ordering before DB writes
                    # (decisions_log INSERT downstream).
                    for d in sorted(decisions, key=lambda x: x.get("symbol") or ""):
                        sym = d.get("symbol")
                        prior = prior_states.get(sym)
                        # Warn when recovering a symbol stuck due to ordering bug
                        if prior and prior.get("state") == "ALLOW" and not prior.get("db_confirmed_at"):
                            logger.warning(
                                "[Decision] Recovering unconfirmed ALLOW state for %s in watchlist %s",
                                sym, wl_id,
                            )
                        should_log, event_type = _should_log_decision(
                            d, prior,
                            score_delta_threshold=dl_score_delta,
                            direction_change_logs=dl_direction_logs,
                        )
                        # Edge-triggered L3_VISIBLE: log only on FIRST appearance
                        # in the L3 ALLOW set (NEW transition handled by
                        # _should_log_decision). Subsequent cycles with the same
                        # symbol stable in ALLOW are intentionally silent — the
                        # frontend reads pipeline_watchlist_assets for "currently
                        # visible" state; decisions_log is an audit trail of
                        # transitions, not a per-cycle snapshot.
                        if d.get("decision") == "ALLOW":
                            current_l3_visibility.add(sym)
                            if not should_log and sym not in prior_visibility:
                                should_log = True
                                event_type = "L3_VISIBLE"
                        if _ml_gate_enabled and sym in _ml_gate_scores:
                            should_log = True
                            event_type = "ML_GATE_ALLOWED" if d.get("decision") == "ALLOW" else "ML_GATE_BLOCKED"
                        new_states[sym] = {
                            "state": d.get("decision"),
                            "score": d.get("score"),
                            "direction": d.get("direction"),
                            "saved_at": datetime.now(timezone.utc).isoformat(),
                            # Preserve db_confirmed_at from prior for filtered symbols
                            "db_confirmed_at": prior.get("db_confirmed_at") if prior else None,
                        }
                        if should_log:
                            d["event_type"] = event_type
                            d["_profile_id"]      = wl.profile_id
                            d["_profile_name"]    = _wl_profile_name
                            d["_profile_version"] = _wl_profile_version
                            decisions_to_log.append(d)
                    # ── L3_VISIBLE diagnostic (TEMP) — remove once root cause confirmed ──
                    _event_breakdown: dict = {}
                    for _d in decisions_to_log:
                        _et = _d.get("event_type") or "?"
                        _event_breakdown[_et] = _event_breakdown.get(_et, 0) + 1
                    logger.info(
                        "[L3_DIAG] wl=%s decisions_to_log=%d prior_visibility=%d current_visibility=%d events=%s",
                        wl.name, len(decisions_to_log),
                        len(prior_visibility), len(current_l3_visibility),
                        _event_breakdown or "{}",
                    )
                    # ─────────────────────────────────────────────────────────
                    # IMPORTANT: persist to DB FIRST, then update Redis.
                    # If DB fails, Redis must NOT advance — otherwise the symbol
                    # gets stuck as ALLOW with no DB record and is silently
                    # filtered forever (ordering bug, Task #109).
                    #
                    # The decision log INSERT is wrapped in a SAVEPOINT so that
                    # a DB-level failure (e.g. missing columns from migration 026)
                    # only rolls back the savepoint and leaves the parent session
                    # healthy for _upsert_assets / _update_last_scanned below.
                    decision_payloads = []
                    try:
                        async with db.begin_nested():
                            decision_payloads = await _persist_decision_logs(db, wl.user_id, decisions_to_log)
                            if _ml_gate_enabled and _ml_gate_scores and decision_payloads:
                                from sqlalchemy import text as _ml_link_text
                                for _p in decision_payloads:
                                    _psym = _p.get("symbol")
                                    _pid = _p.get("id")
                                    _pgate = _ml_gate_scores.get(_psym)
                                    _ranking_id = (_pgate or {}).get("ranking_id")
                                    if not _pid or not _ranking_id:
                                        continue
                                    await db.execute(
                                        _ml_link_text("""
                                            UPDATE ml_opportunity_rankings
                                               SET decision_id = :decision_id
                                             WHERE id = CAST(:ranking_id AS UUID)
                                               AND decision_id IS NULL
                                        """),
                                        {
                                            "decision_id": _pid,
                                            "ranking_id": _ranking_id,
                                        },
                                    )
                            # Stamp db_confirmed_at on each successfully persisted symbol
                            if decisions_to_log:
                                _confirmed_at = datetime.now(timezone.utc).isoformat()
                                for _d in decisions_to_log:
                                    _sym = _d.get("symbol")
                                    if _sym in new_states:
                                        new_states[_sym]["db_confirmed_at"] = _confirmed_at
                    except Exception as _dl_exc:
                        logger.error(
                            "FATAL: Decision persistence failed for watchlist %s: %s "
                            "— verify migration 026 (direction/event_type columns) is applied",
                            wl_id, _dl_exc, exc_info=True
                        )
                        # CRITICAL: Re-raise exception to prevent silent failure
                        raise RuntimeError(
                            f"Decision persistence failed for watchlist {wl_id}: {_dl_exc}"
                        ) from _dl_exc
                    _save_decision_states(redis, wl_id, new_states)
                    # Refresh visibility sets AFTER successful DB write — same
                    # ordering invariant as decision_states (Task #109): Redis
                    # must never advance ahead of the DB or symbols get stuck
                    # without a log row in the current presence cycle.
                    _save_l3_visibility(redis, wl_id, current_l3_visibility)
                    await _upsert_assets(db, wl_id, signals, filters_json, execution_id=execution_id)
                    await _update_last_scanned(db, wl_id)

                    if decision_payloads:
                        from ..services.realtime_bridge import publish_decision_event
                        for payload in decision_payloads:
                            publish_decision_event(payload)

                        # P0 fix: close the orphaned-decisions gap.
                        # _persist_decision_logs + _update_last_scanned have
                        # already committed.  Create shadows inline now instead
                        # of waiting up to 5 min for the next monitor beat.
                        # ON CONFLICT (decision_id) DO NOTHING makes this safe
                        # even when the monitor runs concurrently.
                        _allow_decision_ids = [
                            p["id"] for p in decision_payloads
                            if p.get("decision") == "ALLOW" and p.get("id")
                        ]
                        if _allow_decision_ids:
                            from ..services.shadow_trade_service import (
                                create_shadows_for_new_decisions,
                            )
                            await create_shadows_for_new_decisions(
                                wl.user_id, _allow_decision_ids,
                                watchlist_id=str(wl.id),
                                watchlist_name=wl.name,
                                watchlist_level=wl.level,
                                source_watchlist_id=str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                profile_id=str(wl.profile_id) if wl.profile_id else None,
                                profile_name=_wl_profile_name,
                                profile_version=_wl_profile_version,
                                # Fase 8 (audit 2026-06-24): thread the L3 ML
                                # gate score computed above straight into the
                                # shadow row at creation time instead of
                                # leaving ml_probability/model_lane NULL until
                                # a later /api/ml/orchestrator/backfill call.
                                ml_scores_by_symbol=_ml_gate_scores,
                            )

                        # ── Shadow Bypass Score Gate ──────────────────────────────────────
                        # SHADOW_BYPASS_SCORE_GATE=true: passa os assets rejeitados pelo
                        # min_alpha_score gate pelo _evaluate_l3_decisions completo e cria
                        # shadow trades para os que seriam ALLOW.
                        #
                        # Objetivo: medir se assets de baixo score teriam bom desempenho
                        # caso o gate fosse removido — sem expor capital real.
                        #
                        # Garantias:
                        # * Nunca adiciona à lista `signals` → zero risco de trade real.
                        # * Persiste em decisions_log com metrics.bypass_score_gate=True
                        #   para rastreabilidade e filtro em analytics.
                        # * Qualquer falha é suprimida (não bloqueia o pipeline).
                        import os as _bypass_os
                        _bypass_shadow_enabled = (
                            _bypass_os.getenv("SHADOW_BYPASS_SCORE_GATE", "false").lower() == "true"
                        )
                        if _bypass_shadow_enabled and _gate_rejected:
                            try:
                                _bypass_decisions = await _evaluate_l3_decisions(
                                    _gate_rejected,
                                    profile_config,
                                    level,
                                    score_config=score_config,
                                    db=db,
                                    user_id=wl.user_id,
                                    pool_id=wl.source_pool_id,
                                )
                                _bypass_allow = [
                                    d for d in _bypass_decisions
                                    if d.get("decision") == "ALLOW"
                                ]
                                if _bypass_allow:
                                    for _bd in _bypass_allow:
                                        _bm = _bd.get("metrics") or {}
                                        _bm["bypass_score_gate"] = True
                                        _bm["bypass_score_value"] = float(
                                            _bd.get("score") or 0
                                        )
                                        _bd["metrics"] = _bm
                                    _bypass_payloads: list = []
                                    try:
                                        async with db.begin_nested():
                                            _bypass_payloads = await _persist_decision_logs(
                                                db, wl.user_id, _bypass_allow
                                            )
                                    except Exception as _bp_persist_exc:
                                        logger.warning(
                                            "[BypassShadow] persist failed wl=%s: %s",
                                            wl.name, _bp_persist_exc,
                                        )
                                    _bypass_ids = [
                                        p["id"] for p in _bypass_payloads if p.get("id")
                                    ]
                                    if _bypass_ids:
                                        from ..services.shadow_trade_service import (
                                            create_shadows_for_new_decisions as _create_bypass_shadows,
                                        )
                                        await _create_bypass_shadows(
                                            wl.user_id, _bypass_ids,
                                            watchlist_id=str(wl.id),
                                            watchlist_name=wl.name,
                                            watchlist_level=wl.level,
                                            source_watchlist_id=str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                            profile_id=str(wl.profile_id) if wl.profile_id else None,
                                            profile_name=_wl_profile_name,
                                            profile_version=_wl_profile_version,
                                        )
                                        logger.info(
                                            "[BypassShadow] wl=%s: %d score-bypassed"
                                            " → %d L3-ALLOW → %d shadows",
                                            wl.name,
                                            len(_gate_rejected),
                                            len(_bypass_allow),
                                            len(_bypass_ids),
                                        )
                                else:
                                    logger.info(
                                        "[BypassShadow] wl=%s: %d score-bypassed"
                                        " → 0 L3-ALLOW (todos falhariam L3 de qualquer forma)",
                                        wl.name, len(_gate_rejected),
                                    )
                            except Exception as _bypass_exc:
                                logger.warning(
                                    "[BypassShadow] wl=%s falhou (non-blocking): %s",
                                    wl.name, _bypass_exc,
                                )

                        # ML Gate — write ml_predictions rows now that we have decision IDs.
                        # Only fires when ML gate was active AND produced scores.
                        if _ml_gate_enabled and _ml_gate_scores:
                            try:
                                from sqlalchemy import text as _sql_text
                                _ml_pred_rows = []
                                for _p in decision_payloads:
                                    _pid = _p.get("id")
                                    _psym = _p.get("symbol")
                                    _pgate = _ml_gate_scores.get(_psym)
                                    if not _pid or not _psym or not _pgate:
                                        continue
                                    _ml_pred_rows.append({
                                        "model_id": _pgate.get("model_id"),
                                        "decision_id": _pid,
                                        "symbol": _psym,
                                        "probability": _pgate.get("probability"),
                                        "approved": bool(_pgate.get("approved", False)),
                                        "threshold": _pgate.get("threshold"),
                                        "model_lane": _pgate.get("model_lane"),
                                        "reason_code": _pgate.get("reason_code"),
                                        "score_status": _pgate.get("score_status"),
                                        "promotion_gate_status": _pgate.get("promotion_gate_status"),
                                        "gate_payload": _pgate.get("gate_payload") or {},
                                    })
                                if _ml_pred_rows:
                                    async with db.begin_nested():
                                        await db.execute(
                                            _sql_text("""
                                                INSERT INTO ml_predictions
                                                    (model_id, decision_id, symbol,
                                                     win_fast_probability, model_approved,
                                                     threshold_used, model_lane, reason_code,
                                                     score_status, promotion_gate_status,
                                                     gate_payload)
                                                SELECT
                                                    CAST(NULLIF(r.model_id, '') AS UUID),
                                                    r.decision_id,
                                                    r.symbol,
                                                    r.probability,
                                                    r.approved,
                                                    r.threshold,
                                                    r.model_lane,
                                                    r.reason_code,
                                                    r.score_status,
                                                    r.promotion_gate_status,
                                                    r.gate_payload
                                                FROM jsonb_to_recordset(CAST(:rows AS jsonb))
                                                    AS r(model_id text, decision_id int,
                                                         symbol text, probability float,
                                                         approved bool, threshold float,
                                                         model_lane text, reason_code text,
                                                         score_status text,
                                                         promotion_gate_status text,
                                                         gate_payload jsonb)
                                                ON CONFLICT DO NOTHING
                                            """),
                                            {"rows": __import__("json").dumps(_ml_pred_rows)},
                                        )
                                    logger.info(
                                        "[MLGate] logged %d ml_predictions rows for wl=%s",
                                        len(_ml_pred_rows), wl.name,
                                    )
                            except Exception as _ml_log_exc:
                                logger.warning(
                                    "[MLGate] ml_predictions write failed for wl=%s: %s",
                                    wl.name, _ml_log_exc,
                                )

                    # ── L3_REJECTED shadows — fora de if decision_payloads ────────────────
                    # Captura TODOS os ativos que chegam ao L3 mas não recebem ALLOW:
                    # 1. Ativos que passaram os filtros do profile mas foram bloqueados
                    #    pelas entry_triggers (decisions com decision=BLOCK).
                    # 2. Ativos que foram rejeitados pelos filtros do profile antes de
                    #    chegar ao _evaluate_l3_decisions (profile_passed=0 quando mercado
                    #    não satisfaz condições de momentum/tendência dos filtros L3).
                    # Sem incluir (2), L3_REJECTED fica sistematicamente vazio sempre que
                    # o mercado não satisfaz os filtros — perdendo todos os dados ML.
                    _allowed_syms_l3 = {
                        d.get("symbol") for d in decisions if d.get("decision") == "ALLOW"
                    }
                    _all_block_decisions = [
                        d for d in decisions if d.get("decision") == "BLOCK"
                    ]
                    # Ativos rejeitados pelo filtro do profile (nunca entraram em decisions)
                    _decided_syms = {d.get("symbol") for d in decisions}
                    _filter_rejected_block = []
                    for _fa in assets:
                        _fsym = _fa.get("symbol")
                        if not _fsym or _fsym in _allowed_syms_l3 or _fsym in _decided_syms:
                            continue
                        _find = dict(_fa.get("indicators") or {})
                        for _ctx_key in _DECISION_CONTEXT_SNAPSHOT_FIELDS:
                            if _ctx_key in _fa and _fa.get(_ctx_key) is not None:
                                _find[_ctx_key] = _fa.get(_ctx_key)
                        _filter_rejected_block.append({
                            "symbol": _fsym,
                            "strategy": level,
                            "decision": "BLOCK",
                            "score": _fa.get("_score") or _fa.get("alpha_score") or 0,
                            "direction": (
                                _fa.get("futures_direction")
                                or ("NEUTRAL" if _fa.get("is_futures") else "SPOT")
                            ),
                            "reasons": [{"reason": "profile_filter_rejected", "stage": "L3"}],
                            "metrics": {
                                "indicators_snapshot": {
                                    k: {"value": v}
                                    for k, v in _find.items()
                                    if v is not None
                                },
                                "source": "l3_filter_rejected",
                            },
                            "_asset": _fa,
                        })
                    _all_block_candidates = _all_block_decisions + _filter_rejected_block
                    if _all_block_candidates:
                        try:
                            from ..services.shadow_trade_service import (
                                create_l3_rejected_inline_shadows,
                            )
                            await create_l3_rejected_inline_shadows(
                                user_id=wl.user_id,
                                decisions=_all_block_candidates,
                                execution_id=str(execution_id),
                                promotion_at=datetime.now(timezone.utc),
                                watchlist_id=str(wl.id),
                                watchlist_name=wl.name,
                                watchlist_level=wl.level,
                                source_watchlist_id=str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                profile_id=str(wl.profile_id) if wl.profile_id else None,
                                profile_name=_wl_profile_name,
                                profile_version=_wl_profile_version,
                            )
                        except Exception as _l3rej_exc:
                            logger.warning(
                                "[PipelineScan] L3_REJECTED capture failed (%s)"
                                " — L3 stream unaffected",
                                _l3rej_exc,
                            )

                    # ── L3_SIMULATED shadows — fora de if decision_payloads ───────────────
                    # Controlado por ML config: shadow_capture_l3_simulated_enabled.
                    if decisions:
                        try:
                            from ..services.shadow_trade_service import (
                                create_l3_simulated_shadows,
                            )
                            await create_l3_simulated_shadows(
                                user_id=wl.user_id,
                                decisions=decisions,
                                execution_id=str(execution_id),
                                promotion_at=datetime.now(timezone.utc),
                                watchlist_id=str(wl.id),
                                watchlist_name=wl.name,
                                watchlist_level=wl.level,
                                source_watchlist_id=str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                profile_id=str(wl.profile_id) if wl.profile_id else None,
                                profile_name=_wl_profile_name,
                                profile_version=_wl_profile_version,
                            )
                        except Exception as _l3sim_exc:
                            logger.warning(
                                "[PipelineScan] L3_SIMULATED capture failed (%s)"
                                " — L3 stream unaffected",
                                _l3sim_exc,
                            )

                    # ── Strategy Lab: evaluate all active lab profiles against L3 assets ──
                    # Piggybacks on any L3 scan — no separate watchlist per profile needed.
                    # Each lab profile independently re-evaluates the L2-approved asset pool
                    # with its own rules. Live order flow injection is skipped for speed
                    # (lab runs on the same cached snapshot as the main L3 evaluation).
                    try:
                        import json as _json
                        _lab_rows = (await db.execute(
                            text("""
                                SELECT id, name, config, updated_at
                                FROM profiles
                                WHERE is_active = true
                                  AND user_id = :uid
                                  AND name LIKE 'L3!_%' ESCAPE '!'
                                LIMIT 50
                            """),
                            {"uid": str(wl.user_id)},
                        )).fetchall()
                        logger.info(
                            "[StrategyLab] wl=%s assets=%d lab_profiles=%d",
                            wl.name, len(assets), len(_lab_rows),
                        )
                        if _lab_rows and assets:
                            from ..services.shadow_trade_service import (
                                create_strategy_lab_shadows as _create_lab_allow,
                                create_strategy_lab_rejected_shadows as _create_lab_rejected,
                            )
                            _lab_assets_by_sym = {a["symbol"]: a for a in assets}
                            for _lp in _lab_rows:
                                try:
                                    # asyncpg returns JSONB as a string from text() queries
                                    _raw_cfg = _lp.config
                                    _lp_cfg = (
                                        _json.loads(_raw_cfg)
                                        if isinstance(_raw_cfg, str)
                                        else (_raw_cfg or {})
                                    ) or {}
                                    _lp_passed, _ = evaluate_rejections(
                                        assets,
                                        profile_config=_lp_cfg,
                                        stage="L3",
                                        profile_id=str(_lp.id),
                                    )
                                    _lp_decs = await _evaluate_l3_decisions(
                                        _lp_passed,
                                        _lp_cfg,
                                        level,
                                    )
                                    _lp_allow = [d for d in _lp_decs if d.get("decision") == "ALLOW"]
                                    _lp_block = [d for d in _lp_decs if d.get("decision") == "BLOCK"]
                                    logger.info(
                                        "[StrategyLab] %s → passed=%d ALLOW=%d BLOCK=%d",
                                        _lp.name, len(_lp_passed), len(_lp_allow), len(_lp_block),
                                    )
                                    if _lp_allow:
                                        await _create_lab_allow(
                                            user_id=wl.user_id,
                                            profile_id=_lp.id,
                                            profile_version=_lp.updated_at,
                                            profile_name=_lp.name,
                                            strategy_type="L3_STRATEGY_LAB",
                                            rules_snapshot=_lp_cfg,
                                            allow_decisions=_lp_allow,
                                            assets_by_symbol=_lab_assets_by_sym,
                                            execution_id=str(execution_id),
                                            promotion_at=datetime.now(timezone.utc),
                                            db=db,
                                            watchlist_id=str(wl.id),
                                            watchlist_name=wl.name,
                                            watchlist_level=wl.level,
                                            source_watchlist_id=str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                        )
                                    if _lp_block:
                                        await _create_lab_rejected(
                                            user_id=wl.user_id,
                                            profile_id=_lp.id,
                                            profile_version=_lp.updated_at,
                                            profile_name=_lp.name,
                                            strategy_type="L3_STRATEGY_LAB",
                                            rules_snapshot=_lp_cfg,
                                            block_decisions=_lp_block,
                                            assets_by_symbol=_lab_assets_by_sym,
                                            execution_id=str(execution_id),
                                            promotion_at=datetime.now(timezone.utc),
                                            db=db,
                                            watchlist_id=str(wl.id),
                                            watchlist_name=wl.name,
                                            watchlist_level=wl.level,
                                            source_watchlist_id=str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
                                        )
                                except Exception as _lp_exc:
                                    logger.warning(
                                        "[StrategyLab] profile %s failed: %s",
                                        _lp.name, _lp_exc,
                                    )
                    except Exception as _lab_exc:
                        logger.warning(
                            "[StrategyLab] multi-profile evaluation failed wl=%s: %s",
                            wl.name, _lab_exc,
                        )

                    if new_syms:
                        stats["new_signals"] += len(new_syms)
                        await _broadcast_pipeline_update(
                            watchlist_id=wl_id,
                            watchlist_name=wl.name,
                            level="L3",
                            new_symbols=new_syms,
                            all_signals=signals,
                        )

                except Exception as exc:
                    logger.exception("[PipelineScan] Error processing watchlist %s: %s", wl.name, exc)
                    stats["errors"] += 1
                    # Roll back any failed transaction so subsequent watchlists
                    # are not affected by an InFailedSQLTransactionError cascade.
                    # Surface rollback failures at WARNING — they used to be
                    # silently swallowed and were the entry point for the
                    # cascade tracked in Task #125.
                    try:
                        await db.rollback()
                    except Exception as _rb_exc:
                        logger.warning(
                            "[PipelineScan] %s: rollback after watchlist failure raised %s: %s "
                            "— session may be unusable for subsequent watchlists",
                            wl.name, type(_rb_exc).__name__, _rb_exc,
                        )
                    continue

        # Run the integrity check on a *fresh* session.  Defense-in-depth: even
        # if the per-watchlist loop accidentally leaks an aborted-tx state into
        # the loop session, a brand-new session for integrity guarantees the
        # final SELECT/UPDATE pair won't see InFailedSQLTransactionError
        # (Task #125).
        async with AsyncSessionLocal() as integrity_db:
            stats["integrity"] = await validate_pipeline_integrity(
                integrity_db,
                wl_rows=wl_snapshots,
                profile_config_map=profile_config_map,
                execution_id=execution_id,
            )

    logger.info(
        "[PipelineScan] Done — watchlists=%d  new_signals=%d  errors=%d",
        stats["watchlists"], stats["new_signals"], stats["errors"],
    )
    return stats


# ─── Celery task ──────────────────────────────────────────────────────────────

def _sync_redis_client():
    try:
        import redis
        from ..config import settings

        return redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
    except Exception as exc:
        logger.warning("[PipelineScan] Redis marker unavailable: %s", exc)
        return None


def _record_success_marker() -> None:
    client = _sync_redis_client()
    if client is None:
        return
    try:
        client.set(_LAST_SUCCESS_KEY, datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        logger.warning("[PipelineScan] Could not persist success marker: %s", exc)


def _last_success_age_seconds() -> Optional[float]:
    client = _sync_redis_client()
    if client is None:
        return None
    try:
        raw = client.get(_LAST_SUCCESS_KEY)
        if not raw:
            return None
        recorded_at = datetime.fromisoformat(raw)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (datetime.now(timezone.utc) - recorded_at).total_seconds(),
        )
    except Exception as exc:
        logger.warning("[PipelineScan] Could not read success marker: %s", exc)
        return None


@celery_app.task(name="app.tasks.pipeline_scan.safety_net", max_retries=0)
def safety_net():
    """Enqueue a scan only when the canonical 5-minute chain is stale."""
    age_seconds = _last_success_age_seconds()
    if age_seconds is not None and age_seconds < _SAFETY_STALE_SECONDS:
        logger.info(
            "[PipelineScan] Safety net skipped: last success %.2fs ago",
            age_seconds,
        )
        return {"status": "skipped", "last_success_age_seconds": age_seconds}

    from . import task_dispatch

    task_id = task_dispatch.enqueue(
        "app.tasks.pipeline_scan.scan",
        dedup_key="pipeline_scan",
        ttl_seconds=660,
        expires_seconds=_SCAN_MESSAGE_EXPIRES_SECONDS,
    )
    status = "enqueued" if task_id else "dedup_skipped"
    logger.info(
        "[PipelineScan] Safety net %s: last_success_age_seconds=%s task_id=%s",
        status,
        age_seconds,
        task_id,
    )
    return {
        "status": status,
        "last_success_age_seconds": age_seconds,
        "task_id": task_id,
    }


@celery_app.task(name="app.tasks.pipeline_scan.scan", bind=True, max_retries=0)
def scan(self):
    """Periodic pipeline scan — L1 filter → L2 ranking → L3 signals (5 min)."""
    age_seconds = _last_success_age_seconds()
    if age_seconds is not None and age_seconds < _SCAN_COALESCE_SECONDS:
        logger.info(
            "[PipelineScan] Coalesced stale duplicate: last success %.2fs ago",
            age_seconds,
        )
        return {
            "status": "skipped_recent_success",
            "last_success_age_seconds": age_seconds,
        }
    logger.info("[PipelineScan] Starting pipeline scan…")
    try:
        result = _run_async(_run_pipeline_scan())
        if int(result.get("errors", 0)) == 0:
            _record_success_marker()
        logger.info("[PipelineScan] Result: %s", result)
        return result
    except Exception as exc:
        logger.exception("[PipelineScan] Fatal error: %s", exc)
        raise
