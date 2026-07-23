"""Shadow Trade Monitor — Fase 3.

Avança shadow trades em PENDING/RUNNING candle-a-candle (1m) até
atingir TP, SL ou timeout, e replica o resultado em
``trade_simulations`` com ``source='SHADOW'`` (alimenta
``DatasetBuilder.load_simulations``).

Regras
------
* **SL antes de TP** na mesma candle (regra conservadora explícita).
  Quando ``low <= sl_price`` E ``high >= tp_price`` na mesma candle,
  assumimos SL_HIT — pior caso para o trader.
* Timeout: ``timeout_candles`` vem de ``config_snapshot``
  (``SHADOW_TIMEOUT_CANDLES`` env quando ausente).
* Batch máximo ``SHADOW_MONITOR_BATCH_SIZE`` (default 50) por execução,
  iterando IDs em ordem ``sorted()`` (deadlock-safety, gotcha #251/#273).
* Janela máxima por shadow ``SHADOW_MONITOR_MAX_CANDLES_PER_RUN``
  (default 720 = 12 h de 1m) — evita um shadow muito antigo monopolizar
  o ciclo. Se passar disso sem outcome, atualiza ``last_processed_time``
  e o próximo tick continua.
* ``acks_late=False`` (idempotente, beat re-roda; gotcha #245).

Knobs (env)
-----------
* ``SHADOW_MONITOR_INTERVAL_S`` (default 300) — beat schedule.
* ``SHADOW_MONITOR_BATCH_SIZE`` (default 50).
* ``SHADOW_FAST_SCAN_BATCH_SIZE`` (default 20).
* ``SHADOW_MONITOR_MAX_CANDLES_PER_RUN`` (default 720).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text

from .celery_app import celery_app
from ..config import settings
from ..models.shadow_trade import ShadowTrade
from ..services import exit_metrics, indicators_provider, shadow_trade_service

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


SHADOW_MONITOR_BATCH_SIZE = _env_int("SHADOW_MONITOR_BATCH_SIZE", 50)
SHADOW_FAST_SCAN_BATCH_SIZE = _env_int("SHADOW_FAST_SCAN_BATCH_SIZE", 20)
SHADOW_MONITOR_MAX_CANDLES_PER_RUN = _env_int("SHADOW_MONITOR_MAX_CANDLES_PER_RUN", 720)
SHADOW_MONITOR_POST_COMMIT_BUDGET_SECONDS = _env_int(
    "SHADOW_MONITOR_POST_COMMIT_BUDGET_SECONDS", 45
)

# Sources elegíveis para fechamento automático por barreira TP/SL.
# Centralizado aqui — não usar allowlist incompleta dispersa no código.
SHADOW_CLOSABLE_SOURCES: frozenset[str] = frozenset({
    "L3",
    "L3_LAB",
    "L3_REJECTED",
    "L3_SIMULATED",
    "L1_SPECTRUM",
    "STRATEGY_LAB",
    "PI_CHAMPION_CONTROL",
    "PI_CHALLENGER",
})

# Preço stale: se market_metadata.last_updated for mais antigo que este limite,
# não fechar pelo preço (pode ser falso positivo por ticker inativo).
SHADOW_BARRIER_STALE_SECONDS = _env_int("SHADOW_BARRIER_STALE_SECONDS", 300)

# ATR computation for atr_pct_at_entry (Fase 3 — migration 071).
# Period/timeframe via env so they are changeable without a deploy.
_SHADOW_ATR_PERIOD = _env_int("SHADOW_ATR_PERIOD", 14)
_SHADOW_ATR_TIMEFRAME = os.environ.get("SHADOW_ATR_TIMEFRAME", "5m")


def _new_post_commit_deadline() -> float:
    return time.monotonic() + max(SHADOW_MONITOR_POST_COMMIT_BUDGET_SECONDS, 1)


async def _run_best_effort_budgeted(
    items,
    worker,
    *,
    deadline: float,
    item_label: str,
) -> tuple[int, int]:
    """Drain best-effort work until the per-run post-commit budget expires."""
    processed = 0
    skipped = 0
    total = len(items)
    for item in items:
        if time.monotonic() >= deadline:
            skipped = total - processed
            logger.warning(
                "[shadow-monitor] post-commit budget exhausted for %s: "
                "processed=%d skipped=%d budget_s=%d",
                item_label,
                processed,
                skipped,
                SHADOW_MONITOR_POST_COMMIT_BUDGET_SECONDS,
            )
            break
        await worker(item)
        processed += 1
    return processed, skipped


async def _load_shadow_force_close_policy(db) -> Dict[str, Any]:
    row = (await db.execute(text("""
        SELECT config_json
        FROM config_profiles
        WHERE config_type = 'ml' AND is_active = true
        LIMIT 1
    """))).fetchone()
    cfg = {}
    if row and row[0]:
        cfg = row[0] if isinstance(row[0], dict) else json.loads(row[0])

    try:
        max_age_hours = float(cfg.get("shadow_max_open_age_hours") or 0)
    except (TypeError, ValueError):
        max_age_hours = 0.0
    policy = str(cfg.get("shadow_force_close_policy") or "").strip()
    return {
        "max_age_hours": max_age_hours,
        "policy": policy,
        "enabled": max_age_hours > 0 and policy == "TIMEOUT_LAST_KNOWN_PRICE",
    }


def _mark_force_close(
    shadow: ShadowTrade,
    *,
    policy: Dict[str, Any],
    reason: str,
    price_source: str,
    age_hours: float,
) -> None:
    marker = {
        "force_closed": True,
        "force_close_policy": policy.get("policy"),
        "force_close_reason": reason,
        "force_close_price_source": price_source,
        "force_close_age_hours": round(age_hours, 4),
    }
    shadow._force_close_marker = marker
    shadow.exit_metrics_json = {**(shadow.exit_metrics_json or {}), **marker}


def _resolve_expired_timeout(
    shadow: ShadowTrade,
    mm_price,
    ohlcv_price,
    entry_price: float,
    now_utc: datetime,
) -> Optional[float]:
    """R3 (2026-07-05): trade vencido fecha por TIMEOUT ANTES de TP/SL corrente.

    Para trade com ``elapsed >= timeout_candles`` (minutos, candles 1m), o
    veredito é TIMEOUT — nunca barreira a preço corrente. Sem candles 1m no
    banco, um "TP corrente" após a janela é epistemicamente ambíguo (o
    cruzamento pode ter ocorrido dias depois da expiração, ou o preço pode
    ter batido o SL primeiro dentro da janela sem detecção); fechar como TP
    inflaria win-rate/EV (F2 do encerramento: TP_HITs com holding 28-31h).
    Labels v2 não são afetados (holding > 4h ⇒ 0).

    Retorna o exit_price (precedência mm > ohlcv > entry, a mesma do
    timeout-elapsed) quando o trade está vencido; None caso contrário.
    """
    timeout_m = int(shadow.timeout_candles or 0)
    if not timeout_m or shadow.entry_timestamp is None:
        return None
    entry_ts = shadow.entry_timestamp
    if entry_ts.tzinfo is None:
        entry_ts = entry_ts.replace(tzinfo=timezone.utc)
    elapsed_m = (now_utc - entry_ts).total_seconds() / 60.0
    if elapsed_m < timeout_m:
        return None
    if mm_price is not None:
        return float(mm_price)
    if ohlcv_price is not None:
        return float(ohlcv_price)
    return entry_price


def _run_async(coro):
    """Run async coroutine in a sync Celery task.

    Task #274 — canonical 5-step teardown. See collect_market_data._run_async
    for the full rationale. Steps: cancel pending tasks → dispose engine →
    hard-terminate asyncpg connections → shutdown_asyncgens → close loop.
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


async def _fetch_candles(
    db, symbol: str, after_ts: datetime, limit: int
) -> List[Dict[str, Any]]:
    """1m OHLCV candles após ``after_ts`` (exclusive), em ordem temporal."""
    res = await db.execute(
        text(
            """
            SELECT time, open, high, low, close
              FROM ohlcv
             WHERE symbol = :s
               AND timeframe = '1m'
               AND time > :t
             ORDER BY time ASC
             LIMIT :lim
            """
        ),
        {"s": symbol, "t": after_ts, "lim": limit},
    )
    return [
        {
            "time": r.time,
            "open": float(r.open) if r.open is not None else None,
            "high": float(r.high) if r.high is not None else None,
            "low": float(r.low) if r.low is not None else None,
            "close": float(r.close) if r.close is not None else None,
        }
        for r in res.fetchall()
    ]


async def _compute_atr_pct(
    db,
    symbol: str,
    entry_price: float,
    period: int = _SHADOW_ATR_PERIOD,
    timeframe: str = _SHADOW_ATR_TIMEFRAME,
) -> Optional[float]:
    """ATR% do símbolo no ponto de entrada: ATR(period, timeframe) / entry_price * 100.

    Usa True Range simples (sem Wilder smoothing) para simplicidade e
    robustez com janelas pequenas. Retorna None se dados insuficientes.
    Nunca propaga exceção — falha = atr_pct_at_entry fica NULL.
    """
    if entry_price <= 0:
        return None
    try:
        res = await db.execute(
            text(
                """
                SELECT high, low, close
                  FROM ohlcv
                 WHERE symbol  = :s
                   AND timeframe = :tf
                 ORDER BY time DESC
                 LIMIT :n
                """
            ),
            {"s": symbol, "tf": timeframe, "n": period + 1},
        )
        rows = res.fetchall()
        if len(rows) < 2:
            return None
        rows = list(reversed(rows))  # oldest → newest
        trs: List[float] = []
        for i in range(1, len(rows)):
            h = float(rows[i].high)
            lo = float(rows[i].low)
            c_prev = float(rows[i - 1].close)
            trs.append(max(h - lo, abs(h - c_prev), abs(lo - c_prev)))
        if not trs:
            return None
        atr = sum(trs[-period:]) / len(trs[-period:])
        return round(atr / entry_price * 100.0, 6)
    except Exception as exc:
        logger.debug(
            "[shadow-monitor] _compute_atr_pct failed symbol=%s tf=%s: %s",
            symbol, timeframe, exc,
        )
        return None


async def _ensure_entry(db, shadow: ShadowTrade) -> bool:
    """Garante que o shadow tem ``entry_price`` + ``entry_timestamp``.

    Novo fluxo (Task 2026-05-13): a criação já tenta preencher entry
    com o preço CORRENTE multi-timeframe (1m/5m/15m/30m), então a
    grande maioria dos shadows nasce em RUNNING. Esse helper cobre:

    * shadows legados criados antes da migration (status PENDING +
      entry NULL — backfill on-the-fly aqui);
    * raros casos onde nem candle 1m, nem 5m/15m/30m estavam
      disponíveis no instante da decisão.

    Estratégia: tenta primeiro o preço corrente multi-tf (mesmo helper
    usado pelo creator). Só recai pro ``_next_1m_open`` legado se nada
    disponível — mantendo o contrato antigo como último recurso.
    """
    if shadow.entry_price is not None and shadow.entry_timestamp is not None:
        return True

    entry_price, entry_ts = await shadow_trade_service._get_current_price_multi_tf(
        db, shadow.symbol
    )
    if entry_price is None or entry_ts is None:
        entry_price, entry_ts = await shadow_trade_service._next_1m_open(
            db, shadow.symbol, shadow.created_at
        )
    if entry_price is None or entry_ts is None:
        return False

    shadow.entry_price = entry_price
    # Guard contra produtor errôneo (storm Cloud SQL 2026-05-19): se
    # algum helper retornar entry_ts não-datetime, o enrich_market_context
    # subsequente vai gerar `timestamp with time zone <= interval` em prod.
    # Logamos e abortamos a entrada — shadow fica PENDING e tenta no próximo ciclo.
    if not isinstance(entry_ts, datetime):
        logger.error(
            "[shadow-monitor] _ensure_entry: entry_ts não-datetime "
            "(type=%s value=%r shadow_id=%s symbol=%s) — abortando entrada",
            type(entry_ts).__name__, entry_ts, shadow.id, shadow.symbol,
        )
        return False
    shadow.entry_timestamp = entry_ts
    # Promove legado a RUNNING assim que a entrada é resolvida — caso
    # contrário, se não houver candles 1m novas neste tick, o trade
    # ficaria PENDING mesmo já tendo entry_price (ressalva do review).
    if shadow.status == "PENDING":
        shadow.status = "RUNNING"
    if shadow.last_processed_time is None:
        shadow.last_processed_time = entry_ts
    if (
        shadow.tp_pct is not None
        and shadow.sl_pct is not None
        and entry_price > 0
    ):
        shadow.tp_price = entry_price * (1 + float(shadow.tp_pct) / 100.0)
        shadow.sl_price = entry_price * (1 - float(shadow.sl_pct) / 100.0)
    return True


def _set_ttt_fast_win_bucket(shadow: ShadowTrade) -> None:
    """Classifica um FAST_WIN em bucket temporal baseado em time_to_tp_minutes."""
    t = shadow.time_to_tp_minutes or 0.0
    if t < 15.0:
        shadow.ttt_fast_win_bucket = "WIN_0_15M"
    elif t < 30.0:
        shadow.ttt_fast_win_bucket = "WIN_15_30M"
    elif t < 60.0:
        shadow.ttt_fast_win_bucket = "WIN_30_60M"
    else:
        shadow.ttt_fast_win_bucket = "WIN_60_180M"


def _compute_ttt_outcome(shadow: ShadowTrade) -> None:
    """Computa o label TTT (FAST_WIN | TIMEOUT) ao fechar o shadow.

    Chamado por _finalize_outcome após preencher holding_seconds e
    max_profit_pct. Requer que shadow.ttt_enabled = TRUE.

    Lógica
    ------
    Se time_to_tp_minutes foi rastreado inline no scan de candles:
      - <= ttt_timeout_minutes → FAST_WIN (ttt_analysis_done=True)
      - >  ttt_timeout_minutes → TIMEOUT  (ttt_analysis_done=True)

    Se time_to_tp_minutes é None mas max_profit_pct >= ttt_tp_pct:
      - O TTT threshold foi atingido mas o tempo não foi capturado
        (caminho live-close ou sem candles 1m) → ttt_analysis_done=False
        para que ttt_analyzer.py preencha via OHLCV.

    Se max_profit_pct < ttt_tp_pct:
      - Nunca atingiu o threshold → TIMEOUT (ttt_analysis_done=True).

    Métricas computadas sempre (se dados disponíveis):
      elapsed_minutes, profit_velocity, profit_velocity_per_hour.
    """
    if not shadow.ttt_enabled:
        return

    # ── elapsed_minutes ──────────────────────────────────────────────────
    if shadow.holding_seconds is not None:
        shadow.elapsed_minutes = round(shadow.holding_seconds / 60.0, 4)

    # ── profit_velocity ───────────────────────────────────────────────────
    if shadow.max_profit_pct is not None and shadow.elapsed_minutes is not None:
        elapsed_safe_min = max(shadow.elapsed_minutes, 1.0)
        elapsed_safe_h = max(shadow.elapsed_minutes / 60.0, 1.0 / 60.0)
        shadow.profit_velocity = round(
            float(shadow.max_profit_pct) / elapsed_safe_min, 6
        )
        shadow.profit_velocity_per_hour = round(
            float(shadow.max_profit_pct) / elapsed_safe_h, 4
        )

    ttt_tp_pct = float(shadow.ttt_tp_pct) if shadow.ttt_tp_pct is not None else 1.0
    ttt_timeout_m = float(shadow.ttt_timeout_minutes) if shadow.ttt_timeout_minutes is not None else 180.0

    if shadow.time_to_tp_minutes is not None:
        # Caminho normal: tempo rastreado inline no scan de candles 1m.
        if shadow.time_to_tp_minutes <= ttt_timeout_m:
            shadow.ttt_outcome = "FAST_WIN"
            shadow.ttt_close_reason = "TP_HIT_IN_WINDOW"
            _set_ttt_fast_win_bucket(shadow)
            logger.info(
                "[ttt] shadow_id=%s symbol=%s FAST_WIN bucket=%s "
                "time_to_tp=%.1fmin elapsed=%.1fmin velocity=%.4f%%/h",
                shadow.id, shadow.symbol, shadow.ttt_fast_win_bucket,
                shadow.time_to_tp_minutes, shadow.elapsed_minutes or 0,
                shadow.profit_velocity_per_hour or 0,
            )
        else:
            shadow.ttt_outcome = "TIMEOUT"
            shadow.ttt_close_reason = "HARD_TIMEOUT"
            logger.info(
                "[ttt] shadow_id=%s symbol=%s TIMEOUT "
                "time_to_tp=%.1fmin > limit=%.0fmin",
                shadow.id, shadow.symbol,
                shadow.time_to_tp_minutes, ttt_timeout_m,
            )
        shadow.ttt_analysis_done = True
        return

    mfe = float(shadow.max_profit_pct) if shadow.max_profit_pct is not None else 0.0
    if mfe >= ttt_tp_pct:
        # TTT threshold atingido mas tempo não capturado (live-close / sem 1m).
        # ttt_analyzer.py preenche time_to_tp_minutes via OHLCV.
        shadow.ttt_analysis_done = False
        logger.debug(
            "[ttt] shadow_id=%s symbol=%s mfe=%.4f >= ttt_tp=%.2f%% "
            "mas time_to_tp não capturado — enviando para ttt_analyzer",
            shadow.id, shadow.symbol, mfe, ttt_tp_pct,
        )
    else:
        # max_profit nunca atingiu o threshold → TIMEOUT definitivo.
        shadow.ttt_outcome = "TIMEOUT"
        shadow.ttt_close_reason = "HARD_TIMEOUT"
        shadow.ttt_analysis_done = True
        logger.info(
            "[ttt] shadow_id=%s symbol=%s TIMEOUT mfe=%.4f < ttt_tp=%.2f%%",
            shadow.id, shadow.symbol, mfe, ttt_tp_pct,
        )


def _finalize_outcome(
    shadow: ShadowTrade,
    outcome: str,
    exit_price: Optional[float],
    exit_ts: Optional[datetime],
    entry_price: float,
) -> None:
    """Aplica os campos finais do shadow ao bater outcome (TP/SL/TIMEOUT).

    Refatoração 2026-05-14: extraído do bloco inline de ``_advance_shadow``
    para que o caminho live-close (introduzido nesta data) reuse a mesma
    lógica de PnL/holding/COMPLETED sem duplicar. Comportamento
    idêntico ao bloco legado — pure refactor.

    Fase Quant 1 (migration 062): computa mae_pct / mfe_pct a partir de
    min/max_price_post_entry acumulados candle-a-candle durante RUNNING.
    Não altera TP/SL/timeout — puramente observacional.
    """
    shadow.outcome = outcome
    shadow.exit_price = exit_price
    shadow.exit_timestamp = exit_ts
    shadow.label_resolved_at = exit_ts
    if entry_price > 0 and exit_price is not None:
        pnl_pct = (exit_price - entry_price) / entry_price * 100.0
        shadow.pnl_pct = pnl_pct
        shadow.pnl_usdt = float(shadow.amount_usdt) * pnl_pct / 100.0
    if shadow.entry_timestamp and exit_ts:
        shadow.holding_seconds = int(
            (exit_ts - shadow.entry_timestamp).total_seconds()
        )
    # ── MAE/MFE final computation (Fase Quant 1) ─────────────────────────
    if entry_price > 0:
        if shadow.min_price_post_entry is not None:
            # Clamp to ≤ 0: MAE is adverse excursion; positive value (gap-up entry)
            # means no adverse move occurred, not a gain — cap at zero.
            mae = min(0.0, (shadow.min_price_post_entry - entry_price) / entry_price * 100.0)
            shadow.mae_pct = mae
            shadow.max_drawdown_pct = mae
        if shadow.max_price_post_entry is not None:
            # Clamp to ≥ 0: MFE is favorable excursion; negative value (gap-down
            # entry) means no favorable move occurred — cap at zero.
            mfe = max(0.0, (shadow.max_price_post_entry - entry_price) / entry_price * 100.0)
            shadow.mfe_pct = mfe
            shadow.max_profit_pct = mfe
    # ── Shadow Instrumentation (migration 071) ───────────────────────────
    # barrier_touched: preserva BOTH_SAME_CANDLE setado pelo loop candle;
    # preenche TP/SL/NONE para os demais caminhos (live-close, elapsed-timeout).
    if shadow.barrier_touched is None:
        if outcome == "TP_HIT":
            shadow.barrier_touched = "TP"
        elif outcome == "SL_HIT":
            shadow.barrier_touched = "SL"
        else:
            shadow.barrier_touched = "NONE"
    # barrier_touched_at: fallback para exit_ts em caminhos sem loop candle.
    if shadow.barrier_touched_at is None and outcome in ("TP_HIT", "SL_HIT"):
        shadow.barrier_touched_at = exit_ts
    # intrabar_convention: sempre SL_FIRST (convenção conservadora única).
    shadow.intrabar_convention = "SL_FIRST"
    # final_return_pct: retorno com sinal no close do timeout — nulo para TP/SL.
    if outcome == "TIMEOUT" and shadow.pnl_pct is not None:
        shadow.final_return_pct = shadow.pnl_pct
    # net_return_pct / fee_roundtrip_pct_applied: lê do config_snapshot.
    # O valor 0.0 é válido (sem fee) — só pula se a chave não existir.
    _cs = shadow.config_snapshot
    if isinstance(_cs, dict):
        _fee_raw = _cs.get("ml_fee_roundtrip_pct")
        if _fee_raw is not None:
            try:
                _fee = float(_fee_raw)
                shadow.fee_roundtrip_pct_applied = _fee
                if shadow.pnl_pct is not None:
                    shadow.net_return_pct = shadow.pnl_pct - _fee
            except (TypeError, ValueError):
                pass

    shadow.status = "COMPLETED"
    shadow.completed_at = datetime.now(timezone.utc)
    shadow.last_processed_time = exit_ts
    # ── TTT label computation (migration 065) ────────────────────────────
    # Chamado após max_profit_pct e holding_seconds estarem preenchidos.
    # Nunca propaga exceção: falha em TTT não cancela o fechamento do shadow.
    try:
        _compute_ttt_outcome(shadow)
    except Exception as exc:
        logger.warning(
            "[shadow-monitor] _compute_ttt_outcome failed for shadow_id=%s: %s",
            shadow.id, exc,
        )


def _build_exit_metrics_json(
    shadow: ShadowTrade,
    indicator_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    """Constrói exit_metrics_json — snapshot rico de saída (Fase Quant 2).

    Consolida num único JSONB: outcome, PnL, MAE/MFE, preços de entrada/saída,
    e os indicadores flat capturados em features_snapshot_exit.
    Nunca propaga exceção — falha é silenciosa (exit_metrics_json fica NULL).

    Chamado por ``_capture_exit_features`` após gravar features_snapshot_exit,
    portanto shadow já tem mae_pct/mfe_pct preenchidos por _finalize_outcome.
    """
    try:
        entry_price = float(shadow.entry_price) if shadow.entry_price is not None else None
        data: Dict[str, Any] = {
            "outcome": shadow.outcome,
            "pnl_pct": round(float(shadow.pnl_pct), 6) if shadow.pnl_pct is not None else None,
            "pnl_usdt": round(float(shadow.pnl_usdt), 4) if shadow.pnl_usdt is not None else None,
            "holding_seconds": shadow.holding_seconds,
            "entry_price": entry_price,
            "exit_price": float(shadow.exit_price) if shadow.exit_price is not None else None,
            "tp_price": float(shadow.tp_price) if shadow.tp_price is not None else None,
            "sl_price": float(shadow.sl_price) if shadow.sl_price is not None else None,
            "mae_pct": round(float(shadow.mae_pct), 6) if shadow.mae_pct is not None else None,
            "mfe_pct": round(float(shadow.mfe_pct), 6) if shadow.mfe_pct is not None else None,
            "max_drawdown_pct": round(float(shadow.max_drawdown_pct), 6)
                if shadow.max_drawdown_pct is not None else None,
            "max_profit_pct": round(float(shadow.max_profit_pct), 6)
                if shadow.max_profit_pct is not None else None,
            "min_price_post_entry": float(shadow.min_price_post_entry)
                if shadow.min_price_post_entry is not None else None,
            "max_price_post_entry": float(shadow.max_price_post_entry)
                if shadow.max_price_post_entry is not None else None,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        if indicator_snapshot and isinstance(indicator_snapshot, dict):
            if not indicator_snapshot.get("_capture_failed"):
                data["indicators"] = indicator_snapshot
        force_marker = getattr(shadow, "_force_close_marker", None)
        if not isinstance(force_marker, dict) and isinstance(shadow.exit_metrics_json, dict):
            if shadow.exit_metrics_json.get("force_closed") is True:
                force_marker = {
                    key: shadow.exit_metrics_json.get(key)
                    for key in (
                        "force_closed",
                        "force_close_policy",
                        "force_close_reason",
                        "force_close_price_source",
                        "force_close_age_hours",
                    )
                }
        if isinstance(force_marker, dict):
            data.update(force_marker)
        shadow.exit_metrics_json = data
    except Exception as exc:
        logger.debug(
            "[shadow-monitor] _build_exit_metrics_json failed for shadow_id=%s: %s",
            shadow.id, exc,
        )


async def _capture_exit_features(db, shadow: ShadowTrade) -> None:
    """Preenche ``features_snapshot_exit`` com o snapshot completo de
    indicadores na saída (Task #306, fortificado pela Task #312).

    Usa o helper canônico ``indicators_provider.build_full_flat_snapshot``
    — single source of truth do contrato flat ``{key: scalar}`` — para
    capturar o MESMO conjunto de chaves que aparece no snapshot de
    entrada (gravado por ``decisions_log.metrics["indicators_snapshot"]``).
    Isso alimenta o XGBoost com "entrada vs saída" simétricos
    (Task #290: contrato flat preservado).

    Invariante Task #312: este helper NUNCA propaga exceção e NUNCA
    deixa ``features_snapshot_exit`` em ``NULL`` quando chamado para um
    shadow COMPLETED. Em qualquer falha (provider exception, símbolo
    sem indicadores merged, Redis fora, schema drift) gravamos um
    marcador ``{"_capture_failed": True, "_reason": <classificação>,
    "_error"?: <tipo da exceção>}`` para que o frontend distinga
    "snapshot indisponível" de "ainda não capturado" (NULL) e renderize
    uma mensagem informativa em vez de mascarar o NULL como "fechado
    antes da Task #306".

    Continua valendo o invariante D1 (TP/SL/timeout invioláveis): falha
    aqui é best-effort e não anula o outcome — o caller pode logar mas
    não precisa de try/except defensivo extra.
    """
    # Task #316 — captura agora rota pelo helper canônico
    # ``exit_metrics.build_exit_snapshot`` (mesmo path do TradeMonitorService).
    # O helper nunca propaga e devolve dict flat — ou ``{}`` quando o
    # provider está vazio, ou ``{"_capture_error": "..."}`` em falha.
    # Mantemos a UX da Task #312 (marcador estruturado quando NULL não
    # serve) mapeando ``{}`` → ``_capture_failed=indicators_unavailable_at_close``.
    snapshot = await exit_metrics.build_exit_snapshot(db, shadow.symbol)

    if snapshot.get("_capture_error") is not None:
        logger.warning(
            "[shadow-monitor] _capture_exit_features: provider raised for "
            "shadow_id=%s symbol=%s — gravando marcador _capture_failed",
            shadow.id, shadow.symbol,
        )
        shadow.features_snapshot_exit = {
            "_capture_failed": True,
            "_reason": "capture_exception",
            "_error": snapshot["_capture_error"],
        }
        _build_exit_metrics_json(shadow, shadow.features_snapshot_exit)
        return

    if snapshot:
        shadow.features_snapshot_exit = snapshot
        # Best-effort parity check vs entry snapshot (registrado por
        # ``shadow_trade_service._build_features_snapshot`` já em formato flat).
        if settings.ENABLE_EXIT_METRICS_CAPTURE:
            try:
                exit_metrics.validate_parity(
                    shadow.features_snapshot,
                    snapshot,
                    trade_id=shadow.id,
                    outcome=(shadow.outcome or "shadow"),
                )
            except Exception:
                pass
        _build_exit_metrics_json(shadow, snapshot)
        return

    logger.warning(
        "[shadow-monitor] _capture_exit_features: empty snapshot for "
        "shadow_id=%s symbol=%s — provider returned no merged indicators",
        shadow.id, shadow.symbol,
    )
    shadow.features_snapshot_exit = {
        "_capture_failed": True,
        "_reason": "indicators_unavailable_at_close",
    }
    _build_exit_metrics_json(shadow, shadow.features_snapshot_exit)


async def _enrich_market_context(db, shadow: ShadowTrade) -> None:
    """Preenche os 4 campos de contexto de mercado (migration 052).

    Idempotente: só chama o serviço se ALGUM dos 4 campos ainda for NULL
    (uma vez preenchidos no momento da entrada, são imutáveis — o
    contexto é "como era o mercado quando o trade entrou", não muda
    com o tempo). Defesa adicional: nunca propaga exceção, perda do
    enriquecimento não afeta TP/SL/timeout.
    """
    needs_fill = (
        shadow.btc_price_at_entry is None
        or shadow.btc_change_1h_pct is None
        or shadow.funding_rate_at_entry is None
        or shadow.n_concurrent_signals is None
    )
    if not needs_fill or shadow.entry_timestamp is None:
        return
    try:
        ctx = await shadow_trade_service.enrich_market_context(
            db,
            symbol=shadow.symbol,
            entry_timestamp=shadow.entry_timestamp,
            decision_id=shadow.decision_id,
        )
        if shadow.btc_price_at_entry is None and ctx["btc_price_at_entry"] is not None:
            shadow.btc_price_at_entry = ctx["btc_price_at_entry"]
        if shadow.btc_change_1h_pct is None and ctx["btc_change_1h_pct"] is not None:
            shadow.btc_change_1h_pct = ctx["btc_change_1h_pct"]
        if shadow.funding_rate_at_entry is None and ctx["funding_rate_at_entry"] is not None:
            shadow.funding_rate_at_entry = ctx["funding_rate_at_entry"]
        if shadow.n_concurrent_signals is None and ctx["n_concurrent_signals"] is not None:
            shadow.n_concurrent_signals = ctx["n_concurrent_signals"]
    except Exception:
        logger.exception(
            "[shadow-monitor] enrich_market_context failed for shadow_id=%s "
            "— continuing without context (TP/SL/timeout untouched)",
            shadow.id,
        )


async def _enrich_one_async(
    shadow_id: Any,
    symbol: str,
    entry_timestamp: datetime,
    decision_id: Optional[int],
) -> None:
    """Enriquece campos de contexto ML de um shadow em sessão isolada.

    FIX C3 (2026-05-15): enriquecimento movido para fora da tx principal do
    monitor. Qualquer SQL error dentro de ``enrich_market_context`` (ex:
    coluna ausente, lock, timeout) agora aborta apenas ``db_enrich`` — a
    sessão principal já commitou os fechamentos e está encerrada.
    Falha é logada como WARNING e ignorada (best-effort por design).
    """
    from ..database import CeleryAsyncSessionLocal

    try:
        async with CeleryAsyncSessionLocal() as db_enrich:
            async with db_enrich.begin():
                res = await db_enrich.execute(
                    select(ShadowTrade).where(ShadowTrade.id == shadow_id)
                )
                shadow = res.scalar_one_or_none()
                if shadow is None:
                    return
                # Reutiliza o wrapper já fail-safe internamente.
                await _enrich_market_context(db_enrich, shadow)
    except Exception:
        logger.warning(
            "[shadow-monitor] _enrich_one_async falhou para shadow_id=%s "
            "— ignorando (best-effort, fechamentos não afetados)",
            shadow_id,
        )


async def _record_simulation_one_async(shadow_id: Any) -> None:
    """Grava simulação de um shadow COMPLETED em sessão isolada.

    FIX D1 (2026-05-15): record_as_simulation + _capture_exit_features
    movidos para FORA da tx principal do monitor, seguindo EXATAMENTE o
    padrão de _enrich_one_async (FIX C3). Qualquer SQL error (ex: coluna
    ausente em trade_simulations, lock timeout, schema drift) agora aborta
    apenas db_sim — a sessão principal já commitou shadow.status='COMPLETED'
    e está encerrada. Falha é logada como WARNING e ignorada (best-effort).

    FIX Task #312 (2026-05-20): o capture e o INSERT em
    ``trade_simulations`` rodam agora em **transações separadas**. Antes,
    quando ``record_as_simulation`` levantava (hipótese 3 do task),
    o rollback do ``db_sim`` desfazia também a atribuição de
    ``shadow.features_snapshot_exit`` feita por ``_capture_exit_features``
    no mesmo bloco — o trade aparecia COMPLETED com snapshot NULL.

    Agora:
      * **TX1 (capture)**: recarrega o shadow, chama
        ``_capture_exit_features`` (que sempre grava snapshot ou marcador
        ``_capture_failed`` — invariante Task #312) e COMMITA antes de
        sair do bloco. Mesmo se o passo seguinte falhar, o snapshot já
        está persistido.
      * **TX2 (record)**: recarrega o shadow (já com snapshot persistido)
        e chama ``shadow_trade_service.record_as_simulation``. Falha aqui
        agora só afeta ``trade_simulations``; o shadow continua com o
        ``features_snapshot_exit`` correto para a UI/ML.

    Invariante: recarrega o shadow por ID e verifica status == 'COMPLETED'
    antes de gravar — garante idempotência e protege contra rollback externo.
    """
    from ..database import CeleryAsyncSessionLocal

    # ── TX1 — captura do snapshot de saída (commit independente) ────────
    try:
        async with CeleryAsyncSessionLocal() as db_cap:
            async with db_cap.begin():
                res = await db_cap.execute(
                    select(ShadowTrade).where(ShadowTrade.id == shadow_id)
                )
                shadow_cap = res.scalar_one_or_none()
                if shadow_cap is None:
                    return
                if shadow_cap.status != "COMPLETED":
                    # Shadow foi revertido por outra tx — não tocar.
                    return
                # `_capture_exit_features` é fail-safe por contrato
                # (Task #312): sempre grava snapshot ou marcador, nunca
                # propaga. Não precisa de try/except aqui.
                await _capture_exit_features(db_cap, shadow_cap)
    except Exception:
        # Falha estrutural (DB pool, sessão Celery, etc.) — não dá pra
        # gravar nem snapshot nem marcador. Loga e segue: o frontend
        # ainda vai mostrar o fallback "captura não executou" pelo
        # cutoff de data (ver page.tsx).
        logger.warning(
            "[shadow-monitor] _record_simulation_one_async TX1 (capture) "
            "falhou para shadow_id=%s — ignorando (best-effort, fechamento "
            "não afetado)",
            shadow_id,
        )

    # ── TX2 — gravação em trade_simulations (sessão separada) ───────────
    try:
        async with CeleryAsyncSessionLocal() as db_sim:
            async with db_sim.begin():
                res = await db_sim.execute(
                    select(ShadowTrade).where(ShadowTrade.id == shadow_id)
                )
                shadow = res.scalar_one_or_none()
                if shadow is None:
                    return
                if shadow.status != "COMPLETED":
                    return
                await shadow_trade_service.record_as_simulation(db_sim, shadow)
    except Exception:
        logger.warning(
            "[shadow-monitor] _record_simulation_one_async TX2 (record) "
            "falhou para shadow_id=%s — ignorando (best-effort, snapshot "
            "de saída já foi commitado em TX1)",
            shadow_id,
            exc_info=True,
        )


async def _advance_shadow(
    db,
    shadow: ShadowTrade,
    force_close_policy: Optional[Dict[str, Any]] = None,
) -> str:
    """Avança um único shadow trade até outcome ou esgotar candles do tick.

    Retorna um label de transição: ``"completed"``, ``"running"`` ou
    ``"pending"``.
    """
    if not await _ensure_entry(db, shadow):
        # Sem candle 1m disponível ainda — deixa em PENDING, próximo tick.
        return "pending"

    # ── Shadow Instrumentation: barrier metadata (migration 071) ─────────
    # barrier_mode / tp_pct_applied / sl_pct_applied: setados uma vez na
    # primeira resolução de entry_price (fallback para shadows pré-071).
    if shadow.barrier_mode is None:
        shadow.barrier_mode = (shadow.config_snapshot or {}).get(
            "shadow_barrier_mode", "FIXED"
        )
    if shadow.tp_pct_applied is None and shadow.tp_pct is not None:
        shadow.tp_pct_applied = float(shadow.tp_pct)
    if shadow.sl_pct_applied is None and shadow.sl_pct is not None:
        shadow.sl_pct_applied = float(shadow.sl_pct)
    # atr_pct_at_entry: computado uma vez; nunca propaga exceção.
    if shadow.atr_pct_at_entry is None and shadow.entry_price is not None:
        shadow.atr_pct_at_entry = await _compute_atr_pct(
            db, shadow.symbol, float(shadow.entry_price)
        )

    # NOTA: enriquecimento de contexto ML (_enrich_market_context) foi movido
    # para FORA da tx principal — ver _enrich_one_async + _monitor_async.
    # FIX C3 (2026-05-15): SQL error no enrich não deve abortar fechamentos.

    # Sem TP/SL utilizáveis (config sem tp_pct/sl_pct) → não dá pra simular.
    if shadow.tp_price is None or shadow.sl_price is None:
        logger.warning(
            "[shadow-monitor] shadow_id=%s sem tp/sl utilizáveis (tp_pct=%s "
            "sl_pct=%s) — marcado ERROR",
            shadow.id, shadow.tp_pct, shadow.sl_pct,
        )
        shadow.status = "ERROR"
        shadow.completed_at = datetime.now(timezone.utc)
        shadow.label_resolved_at = shadow.exit_timestamp or shadow.completed_at
        return "completed"

    tp = float(shadow.tp_price)
    sl = float(shadow.sl_price)
    entry_price = float(shadow.entry_price)

    # ── Live-close path (Task #292, 2026-05-14): muitos símbolos do
    # pool não têm OHLCV 1m ingerido — só 5m/15m/30m. O scan candle-a-
    # candle 1m abaixo nunca encontrava nada, e trades que VISIVELMENTE
    # já cruzaram o TP no preço atual ficavam parados em RUNNING (a UI
    # mostra current_price > tp_price mas o monitor não fechava).
    #
    # Fonte do preço: ``market_metadata.price`` (refresh ~60s via ticker
    # REST do Gate.io em ``collect_all``), NÃO ``ohlcv``. Iteração #1
    # desta task usou ``_get_current_price_multi_tf`` (close de candle
    # 5m/15m/30m), mas como ``_ensure_entry`` usa o MESMO helper, o
    # ``entry_price`` ficava igual ao ``live_price`` (mesma candle) e o
    # guard ``live_price != entry_price`` rejeitava o fechamento. Além
    # disso, o frontend lê ``market_metadata.price`` para mostrar o
    # current_price ao usuário — usar a mesma fonte garante coerência
    # visual: se a UI mostra preço acima do TP, o monitor fecha.
    #
    # Conservadorismo: se current_price <= sl, registramos SL_HIT
    # (mesma lógica do scan candle). exit_price é fixado em tp/sl
    # para manter o PnL simulado consistente com tp_pct/sl_pct
    # (o scan 1m existente também usa tp/sl, não c["high"]/c["low"]).
    # Lemos AS DUAS fontes de preço corrente e fechamos se QUALQUER uma
    # cruzou TP/SL. Por quê:
    #   - `market_metadata.price` = ticker REST (refresh ~60s).
    #   - `ohlcv` close multi-tf (1m/5m/15m/30m) = mesma fonte usada por
    #     `_fetch_latest_prices` no endpoint `/api/shadow-trades/prices`,
    #     que é o que o frontend mostra como "Atual" para o usuário.
    # As duas podem divergir (ticker passou pelo TP num minuto sem candle
    # fechada, ou ohlcv mais recente que ticker). Avaliar AS DUAS:
    #   - garante coerência visual (UI ≥ TP ⇒ fechamento)
    #   - é mais agressivo no fechamento (qualquer fonte que cruzou já
    #     prova que o preço passou por lá em algum momento)
    try:
        mm_price, mm_ts = await shadow_trade_service._get_market_metadata_price(
            db, shadow.symbol
        )
    except Exception:
        logger.exception(
            "[shadow-monitor] live-close: get_market_metadata_price failed "
            "for shadow_id=%s",
            shadow.id,
        )
        mm_price, mm_ts = None, None

    try:
        ohlcv_price, ohlcv_ts = await shadow_trade_service._get_current_price_multi_tf(
            db, shadow.symbol
        )
    except Exception:
        logger.exception(
            "[shadow-monitor] live-close: get_current_price_multi_tf failed "
            "for shadow_id=%s",
            shadow.id,
        )
        ohlcv_price, ohlcv_ts = None, None

    # Skew guard por fonte: descarta dado anterior ao entry_timestamp
    # (ticker/candle pré-entrada). Mantém a fonte cujo timestamp é
    # >= entry_ts (ou é NULL — sem como caracterizar skew, aceitamos).
    # FIX C2 (2026-05-15): try/except em torno da comparação de datas
    # para cobrir possível mismatch timezone-naive vs timezone-aware
    # (ex: market_metadata.last_updated gravado como TIMESTAMP sem tz
    # enquanto shadow.entry_timestamp é TIMESTAMPTZ). Se a comparação
    # lançar TypeError, aceita a fonte — melhor fechar trade válido com
    # preço ligeiramente impreciso do que nunca fechar (fail-open
    # intencional no skew guard; TP/SL check ainda valida o cruzamento).
    def _ok(price, ts) -> bool:
        if price is None:
            return False
        if ts is None or shadow.entry_timestamp is None:
            return True
        try:
            return ts >= shadow.entry_timestamp
        except TypeError:
            # naive vs aware — aceitar fonte válida (ver FIX C2 acima)
            return True

    candidates: list[tuple[float, Optional[datetime], str]] = []
    if _ok(mm_price, mm_ts):
        candidates.append((float(mm_price), mm_ts, "mm"))
    if _ok(ohlcv_price, ohlcv_ts):
        candidates.append((float(ohlcv_price), ohlcv_ts, "ohlcv"))

    if candidates:
        # Para SL, considera o MENOR preço entre as fontes (mais agressivo
        # no negativo). Para TP, o MAIOR (mais agressivo no positivo).
        # Precedência SL ANTES de TP — mesma ordem do scan 1m legado
        # (`_check_outcome_for_candle`) e do `TradeMonitorService` em
        # produção: política conservadora, perda reconhecida primeiro
        # quando ambos os lados são tocados no mesmo tick.
        max_price, max_ts, max_src = max(candidates, key=lambda c: c[0])
        min_price, min_ts, min_src = min(candidates, key=lambda c: c[0])

        # ── MAE/MFE: update from live-close sources (Fase Quant 1) ──────
        if shadow.min_price_post_entry is None or min_price < shadow.min_price_post_entry:
            shadow.min_price_post_entry = min_price
        if shadow.max_price_post_entry is None or max_price > shadow.max_price_post_entry:
            shadow.max_price_post_entry = max_price

        # ── R3 (2026-07-05): TIMEOUT precede TP/SL para trade vencido ────
        # Ver _resolve_expired_timeout. MAE/MFE acima ainda atualizam
        # (observacionais); o outcome de trade expirado é sempre TIMEOUT.
        _now_utc_r3 = datetime.now(timezone.utc)
        _expired_exit = _resolve_expired_timeout(
            shadow, mm_price, ohlcv_price, entry_price, _now_utc_r3
        )
        if _expired_exit is not None:
            logger.info(
                "[shadow-monitor] timeout-precedence shadow_id=%s symbol=%s "
                "timeout_candles=%s exit_price=%.8f (barreira corrente ignorada "
                "para trade vencido)",
                shadow.id, shadow.symbol, shadow.timeout_candles, _expired_exit,
            )
            _finalize_outcome(shadow, "TIMEOUT", _expired_exit, _now_utc_r3, entry_price)
            return "completed"

        live_outcome: Optional[str] = None
        chosen_price: Optional[float] = None
        chosen_ts: Optional[datetime] = None
        chosen_src: Optional[str] = None
        if min_price <= sl:
            live_outcome = "SL_HIT"
            chosen_price, chosen_ts, chosen_src = min_price, min_ts, min_src
        elif max_price >= tp:
            live_outcome = "TP_HIT"
            chosen_price, chosen_ts, chosen_src = max_price, max_ts, max_src

        if live_outcome is not None:
            outcome = live_outcome
            exit_price = sl if outcome == "SL_HIT" else tp
            # exit_ts = max(chosen_ts, entry_ts) — holding_seconds não
            # negativo. Se `chosen_ts` for NULL, usa entry_timestamp.
            if chosen_ts is None:
                exit_ts = shadow.entry_timestamp or datetime.now(timezone.utc)
            elif shadow.entry_timestamp and chosen_ts < shadow.entry_timestamp:
                exit_ts = shadow.entry_timestamp
            else:
                exit_ts = chosen_ts
            logger.info(
                "[shadow-monitor] live-close shadow_id=%s symbol=%s "
                "outcome=%s src=%s entry=%.8f chosen=%.8f tp=%.8f sl=%.8f "
                "mm=(%s,%s) ohlcv=(%s,%s)",
                shadow.id, shadow.symbol, outcome, chosen_src,
                entry_price, chosen_price, tp, sl,
                mm_price, mm_ts, ohlcv_price, ohlcv_ts,
            )
            _finalize_outcome(shadow, outcome, exit_price, exit_ts, entry_price)
            # FIX D1 (2026-05-15): _capture_exit_features + record_as_simulation
            # movidos para _record_simulation_one_async (sessão isolada, pós-
            # commit). Qualquer SQL error lá não aborta esta tx principal.
            return "completed"

    if (
        force_close_policy
        and force_close_policy.get("enabled")
    ):
        now_utc = datetime.now(timezone.utc)
        opened_at = shadow.created_at or shadow.entry_timestamp
        if opened_at is None:
            opened_at = now_utc
        if opened_at.tzinfo is None:
            opened_at = opened_at.replace(tzinfo=timezone.utc)
        age_hours = (now_utc - opened_at).total_seconds() / 3600.0
        max_age_hours = float(force_close_policy.get("max_age_hours") or 0.0)
        if age_hours >= max_age_hours:
            if mm_price is not None:
                exit_price_force = float(mm_price)
                price_source = "market_metadata"
            elif ohlcv_price is not None:
                exit_price_force = float(ohlcv_price)
                price_source = "ohlcv_latest"
            else:
                exit_price_force = entry_price
                price_source = "entry_price_fallback"
            _mark_force_close(
                shadow,
                policy=force_close_policy,
                reason="max_open_age_exceeded",
                price_source=price_source,
                age_hours=age_hours,
            )
            logger.info(
                "[shadow-monitor] force-close shadow_id=%s symbol=%s age_hours=%.2f "
                "policy=%s exit_price=%.8f source=%s",
                shadow.id, shadow.symbol, age_hours,
                force_close_policy.get("policy"), exit_price_force, price_source,
            )
            _finalize_outcome(
                shadow, "TIMEOUT", exit_price_force, now_utc, entry_price
            )
            return "completed"

    after_ts = shadow.last_processed_time or shadow.entry_timestamp
    candles = await _fetch_candles(
        db, shadow.symbol, after_ts, SHADOW_MONITOR_MAX_CANDLES_PER_RUN
    )
    if not candles:
        # ── FIX B2 (additive, 2026-05-15) — Timeout por elapsed time ───
        # Muitos símbolos do pool não têm OHLCV 1m ingerido; quando o
        # `_fetch_candles` devolve [], o branch TIMEOUT do scan candle-a-
        # candle (abaixo) NUNCA é alcançado e trades ficam RUNNING
        # indefinidamente mesmo após >24h. Aqui fechamos por TIMEOUT
        # quando o tempo decorrido (em minutos) já ultrapassa
        # `timeout_candles` (definido em minutos para candles 1m).
        #
        # Posição CRÍTICA: este early-check vive DENTRO do `if not
        # candles` propositalmente. Se houver candles 1m disponíveis,
        # o loop abaixo prevalece (TP/SL detectados intra-candle ainda
        # têm prioridade sobre TIMEOUT, por candle, mesma regra
        # histórica). Mexer para fora deste `if` quebra a regra
        # canônica TP/SL > TIMEOUT em cenários de retrace (preço tocou
        # TP/SL em candle pendente mas voltou; sem este guard, o
        # TIMEOUT marcaria o trade antes de o scan ver o hit real).
        #
        # Nunca propaga falha (mesma política de `_capture_exit_features`
        # / `record_as_simulation`): se algo aqui levanta, fallback é
        # manter o status atual e tentar de novo no próximo tick.
        timeout_candles_m = int(shadow.timeout_candles or 0) or None
        if timeout_candles_m and shadow.entry_timestamp is not None:
            now_utc = datetime.now(timezone.utc)
            elapsed_minutes = (
                now_utc - shadow.entry_timestamp
            ).total_seconds() / 60.0
            if elapsed_minutes >= timeout_candles_m:
                # exit_price: melhor preço corrente disponível
                # (mm > ohlcv > entry_price). Coerente com a precedência
                # do live-close.
                if mm_price is not None:
                    exit_price_to = float(mm_price)
                elif ohlcv_price is not None:
                    exit_price_to = float(ohlcv_price)
                else:
                    exit_price_to = entry_price
                logger.info(
                    "[shadow-monitor] timeout-elapsed shadow_id=%s "
                    "symbol=%s elapsed_min=%.1f timeout_candles=%d "
                    "exit_price=%.8f entry=%.8f (no 1m candles to scan)",
                    shadow.id, shadow.symbol, elapsed_minutes,
                    timeout_candles_m, exit_price_to, entry_price,
                )
                _finalize_outcome(
                    shadow, "TIMEOUT", exit_price_to, now_utc, entry_price
                )
                # FIX D1 (2026-05-15): _capture_exit_features + record_as_simulation
                # movidos para _record_simulation_one_async (sessão isolada, pós-
                # commit). Qualquer SQL error lá não aborta esta tx principal.
                return "completed"
        # Sem candles novas — mantém status atual.
        return "pending" if shadow.status == "PENDING" else "running"

    # Quantas candles já vimos antes deste tick?
    timeout_candles = int(shadow.timeout_candles or 0) or None
    candles_seen_before = 0
    if timeout_candles and shadow.last_processed_time and shadow.entry_timestamp:
        delta = shadow.last_processed_time - shadow.entry_timestamp
        candles_seen_before = max(int(delta.total_seconds() // 60), 0)

    outcome: Optional[str] = None
    exit_price: Optional[float] = None
    exit_ts: Optional[datetime] = None
    last_seen_ts: Optional[datetime] = None

    # ── TTT: parâmetros para rastreamento inline (migration 065) ─────────
    # Só ativo quando shadow foi criado com ttt_enabled=TRUE.
    # Computamos o preço-alvo TTT (pode ser < tp) uma vez antes do loop.
    ttt_active = bool(shadow.ttt_enabled)
    ttt_tp_price_inline: Optional[float] = None
    if ttt_active and shadow.ttt_tp_pct is not None and entry_price > 0:
        ttt_tp_price_inline = entry_price * (1.0 + float(shadow.ttt_tp_pct) / 100.0)

    for idx, c in enumerate(candles, start=1):
        last_seen_ts = c["time"]
        if c["high"] is None or c["low"] is None:
            continue
        # ── MAE/MFE: update min/max per-candle (Fase Quant 1) ───────────
        # Usa candle.low / candle.high — nunca só o close.
        # Atualiza em TODAS as candles, inclusive antes do outcome (running).
        _cl = c["low"]
        _ch = c["high"]
        if shadow.min_price_post_entry is None or _cl < shadow.min_price_post_entry:
            shadow.min_price_post_entry = _cl
            shadow.mae_at = c["time"]  # migration 071: timestamp do pior MAE
        if shadow.max_price_post_entry is None or _ch > shadow.max_price_post_entry:
            shadow.max_price_post_entry = _ch
            shadow.mfe_at = c["time"]  # migration 071: timestamp do melhor MFE
            # candles_to_peak: atualizado toda vez que registramos novo máximo.
            if ttt_active:
                shadow.candles_to_peak = candles_seen_before + idx

        # ── TTT inline tracking (migration 065) ──────────────────────────
        # Executa APÓS atualizar max_price_post_entry mas ANTES de checar
        # SL/TP — garante que milestones são registrados mesmo quando o TP
        # regular coincide com a candle de milestone.
        if ttt_active:
            candle_abs = candles_seen_before + idx
            c_close = c.get("close")

            # Milestones de lucro máximo por janela temporal.
            # Definição: MAX(high) desde entry até esse candle.
            # Só setamos quando o candle_abs EXATAMENTE cruza o milestone
            # (dentro deste tick). Se o milestone ficou em tick anterior,
            # max_profit_first_Xm permanece NULL para o ttt_analyzer preencher.
            if candle_abs == 15 and shadow.max_profit_first_15m is None and entry_price > 0:
                if shadow.max_price_post_entry is not None:
                    shadow.max_profit_first_15m = round(
                        (shadow.max_price_post_entry - entry_price) / entry_price * 100.0, 6
                    )
            if candle_abs == 30 and shadow.max_profit_first_30m is None and entry_price > 0:
                if shadow.max_price_post_entry is not None:
                    shadow.max_profit_first_30m = round(
                        (shadow.max_price_post_entry - entry_price) / entry_price * 100.0, 6
                    )
            if candle_abs == 60 and shadow.max_profit_first_60m is None and entry_price > 0:
                if shadow.max_price_post_entry is not None:
                    shadow.max_profit_first_60m = round(
                        (shadow.max_price_post_entry - entry_price) / entry_price * 100.0, 6
                    )

            # candles_to_first_positive: primeira candle onde close > entry.
            if shadow.candles_to_first_positive is None and c_close is not None:
                if c_close > entry_price:
                    shadow.candles_to_first_positive = candle_abs

            # time_to_tp_minutes: primeira vez que high >= ttt_tp_price.
            # Registrado UMA VEZ (campo imutável após ser setado).
            if (
                ttt_tp_price_inline is not None
                and shadow.time_to_tp_minutes is None
                and _ch >= ttt_tp_price_inline
            ):
                c_ts = c["time"]
                if c_ts is not None and shadow.entry_timestamp is not None:
                    try:
                        entry_aware = shadow.entry_timestamp
                        c_ts_aware = c_ts
                        # Normalize naive → UTC so subtraction never raises TypeError.
                        if getattr(entry_aware, "tzinfo", None) is None:
                            entry_aware = entry_aware.replace(tzinfo=timezone.utc)
                        if getattr(c_ts_aware, "tzinfo", None) is None:
                            c_ts_aware = c_ts_aware.replace(tzinfo=timezone.utc)
                        delta_s = (c_ts_aware - entry_aware).total_seconds()
                        shadow.time_to_tp_minutes = round(max(delta_s / 60.0, 0.0), 4)
                    except TypeError:
                        # Fallback — ttt_analyzer resolverá via OHLCV.
                        pass

        # SL antes de TP na mesma candle — convenção conservadora (SL_FIRST).
        # Se ambas as barreiras são tocadas no mesmo candle, registra
        # barrier_touched='BOTH_SAME_CANDLE' e resolve como SL (pior caso).
        _sl_hit = c["low"] <= sl
        _tp_hit = c["high"] >= tp
        if _sl_hit:
            if _tp_hit:
                # Ambas barreiras tocadas no mesmo candle 1m.
                shadow.barrier_touched = "BOTH_SAME_CANDLE"
            shadow.barrier_touched_at = c["time"]
            outcome = "SL_HIT"
            exit_price = sl
            exit_ts = c["time"]
            break
        if _tp_hit:
            shadow.barrier_touched = "TP"
            shadow.barrier_touched_at = c["time"]
            outcome = "TP_HIT"
            exit_price = tp
            exit_ts = c["time"]
            break
        # Timeout (count global desde entry, NÃO só dentro do tick atual).
        if timeout_candles and (candles_seen_before + idx) >= timeout_candles:
            outcome = "TIMEOUT"
            exit_price = c["close"] if c["close"] is not None else c["open"]
            exit_ts = c["time"]
            break

    if outcome is None:
        # Não atingiu TP/SL/timeout dentro da janela — registra progresso
        # e passa pra próxima execução.
        shadow.last_processed_time = last_seen_ts
        if shadow.status == "PENDING":
            shadow.status = "RUNNING"
            return "running"
        return "running"

    # Outcome atingido (caminho candle-a-candle 1m) — usa os mesmos
    # helpers do live-close.
    _finalize_outcome(shadow, outcome, exit_price, exit_ts, entry_price)
    # FIX D1 (2026-05-15): _capture_exit_features + record_as_simulation
    # movidos para _record_simulation_one_async (sessão isolada, pós-
    # commit). Qualquer SQL error lá não aborta esta tx principal.
    return "completed"


async def _fast_barrier_scan_async(run_id: str) -> Dict[str, Any]:
    """Fast-scan de barreira: fecha um lote limitado de TP/SL rompidos por tick.

    Roda ANTES do batch regular. Garante que qualquer trade com
    ``market_metadata.price <= sl_price`` ou ``>= tp_price`` seja fechado
    no mesmo tick em que a barreira é detectada — sem depender da rotação
    do batch (que pode levar até 100 min com 1000+ trades abertos).

    Stale guard: ignora market_metadata com last_updated mais antigo que
    ``SHADOW_BARRIER_STALE_SECONDS`` (padrão 300s = 5 min). Preço stale
    pode ser falso positivo por ticker inativo.

    Fontes cobertas: ``SHADOW_CLOSABLE_SOURCES`` (centralizado).
    Idempotente: usa ``FOR UPDATE SKIP LOCKED`` + ``status IN (RUNNING, PENDING)``.
    Auditoria: grava em ``shadow_trade_closure_audit`` (migration 119).
    """
    from ..database import CeleryAsyncSessionLocal
    import uuid as _uuid

    closed_tp = 0
    closed_sl = 0
    skipped_stale = 0
    errors = 0
    sim_targets: List[Any] = []
    source_counts: Dict[str, int] = {}

    try:
        # Phase 1: identify a bounded breached-ID batch (no lock yet).
        async with CeleryAsyncSessionLocal() as db_scan:
            stale_cutoff = datetime.now(timezone.utc) - timedelta(
                seconds=SHADOW_BARRIER_STALE_SECONDS
            )
            res = await db_scan.execute(
                text("""
                    SELECT st.id
                    FROM shadow_trades st
                    JOIN market_metadata mm ON mm.symbol = st.symbol
                    WHERE st.status IN ('RUNNING', 'PENDING')
                      AND st.tp_price IS NOT NULL
                      AND st.sl_price IS NOT NULL
                      AND (mm.price <= st.sl_price OR mm.price >= st.tp_price)
                      AND (mm.last_updated IS NULL OR mm.last_updated >= :stale_cutoff)
                    ORDER BY st.id
                    LIMIT :fast_scan_batch_size
                """),
                {
                    "stale_cutoff": stale_cutoff,
                    "fast_scan_batch_size": SHADOW_FAST_SCAN_BATCH_SIZE,
                },
            )
            breached_ids = [row[0] for row in res.fetchall()]

        # Count stale skips separately (informational)
        try:
            async with CeleryAsyncSessionLocal() as db_stale:
                res2 = await db_stale.execute(
                    text("""
                        SELECT COUNT(*)
                        FROM shadow_trades st
                        JOIN market_metadata mm ON mm.symbol = st.symbol
                        WHERE st.status IN ('RUNNING', 'PENDING')
                          AND st.tp_price IS NOT NULL
                          AND st.sl_price IS NOT NULL
                          AND (mm.price <= st.sl_price OR mm.price >= st.tp_price)
                          AND mm.last_updated IS NOT NULL
                          AND mm.last_updated < :stale_cutoff
                    """),
                    {"stale_cutoff": stale_cutoff},
                )
                skipped_stale = res2.scalar() or 0
        except Exception:
            pass

        if not breached_ids:
            logger.info(
                "[shadow-monitor] fast-scan run_id=%s: no breached barriers found "
                "(stale_skipped=%d)",
                run_id, skipped_stale,
            )
            return {
                "fast_scan_closed_tp": 0,
                "fast_scan_closed_sl": 0,
                "fast_scan_skipped_stale": skipped_stale,
                "fast_scan_errors": 0,
            }

        logger.info(
            "[shadow-closer] fast-scan run_id=%s started open_breached=%d "
            "stale_skipped=%d",
            run_id, len(breached_ids), skipped_stale,
        )

        # Phase 2: lock and process breached trades
        audit_rows: list[dict] = []
        async with CeleryAsyncSessionLocal() as db:
            async with db.begin():
                res = await db.execute(
                    select(ShadowTrade)
                    .where(
                        ShadowTrade.id.in_(breached_ids),
                        ShadowTrade.status.in_(("PENDING", "RUNNING")),
                    )
                    .with_for_update(skip_locked=True)
                    .order_by(ShadowTrade.id.asc())
                )
                shadows = list(res.scalars().all())
                shadows.sort(key=lambda s: s.id)

                for shadow in shadows:
                    try:
                        prev_status = shadow.status
                        transition = await _advance_shadow(db, shadow)
                        if transition == "completed":
                            outcome = shadow.outcome or "UNKNOWN"
                            if outcome == "TP_HIT":
                                closed_tp += 1
                            elif outcome == "SL_HIT":
                                closed_sl += 1
                            src = shadow.source or "UNKNOWN"
                            source_counts[src] = source_counts.get(src, 0) + 1
                            sim_targets.append(shadow.id)
                            audit_rows.append({
                                "shadow_trade_id": str(shadow.id),
                                "source": shadow.source,
                                "symbol": shadow.symbol,
                                "previous_status": prev_status,
                                "entry_price": float(shadow.entry_price) if shadow.entry_price else None,
                                "exit_price": float(shadow.exit_price) if shadow.exit_price else None,
                                "tp_price": float(shadow.tp_price) if shadow.tp_price else None,
                                "sl_price": float(shadow.sl_price) if shadow.sl_price else None,
                                "pnl_pct": float(shadow.pnl_pct) if shadow.pnl_pct else None,
                                "pnl_usdt": float(shadow.pnl_usdt) if shadow.pnl_usdt else None,
                                "closure_reason": outcome,
                                "closer_run_id": run_id,
                            })
                    except Exception:
                        errors += 1
                        logger.exception(
                            "[shadow-closer] fast-scan advance failed shadow_id=%s",
                            shadow.id,
                        )

        # Phase 3: write audit rows (best-effort, separate tx)
        if audit_rows:
            try:
                async with CeleryAsyncSessionLocal() as db_audit:
                    async with db_audit.begin():
                        for row in audit_rows:
                            await db_audit.execute(
                                text("""
                                    INSERT INTO shadow_trade_closure_audit
                                    (shadow_trade_id, source, symbol, previous_status,
                                     entry_price, exit_price, tp_price, sl_price,
                                     pnl_pct, pnl_usdt, closure_reason, closer_run_id)
                                    VALUES
                                    (CAST(:shadow_trade_id AS uuid), :source, :symbol, :previous_status,
                                     :entry_price, :exit_price, :tp_price, :sl_price,
                                     :pnl_pct, :pnl_usdt, :closure_reason, CAST(:closer_run_id AS uuid))
                                """),
                                row,
                            )
            except Exception:
                logger.warning(
                    "[shadow-closer] fast-scan audit write failed run_id=%s — "
                    "closures committed, audit only affected",
                    run_id,
                )

    except Exception:
        logger.exception(
            "[shadow-closer] fast-scan failed run_id=%s — falling back to batch",
            run_id,
        )
        return {
            "fast_scan_closed_tp": closed_tp,
            "fast_scan_closed_sl": closed_sl,
            "fast_scan_skipped_stale": skipped_stale,
            "fast_scan_errors": errors + 1,
        }

    # best-effort sim/capture (post-commit, isolated sessions)
    _, deferred_sim = await _run_best_effort_budgeted(
        sim_targets,
        _record_simulation_one_async,
        deadline=_new_post_commit_deadline(),
        item_label="fast_scan_simulations",
    )

    total_closed = closed_tp + closed_sl
    logger.info(
        "[shadow-closer] fast-scan run_id=%s closed_tp=%d closed_sl=%d "
        "source_breakdown=%s skipped_stale=%d deferred_sim=%d errors=%d",
        run_id,
        closed_tp,
        closed_sl,
        source_counts,
        skipped_stale,
        deferred_sim,
        errors,
    )
    return {
        "fast_scan_closed_tp": closed_tp,
        "fast_scan_closed_sl": closed_sl,
        "fast_scan_skipped_stale": skipped_stale,
        "fast_scan_deferred_simulations": deferred_sim,
        "fast_scan_errors": errors,
    }


async def _monitor_async() -> Dict[str, Any]:
    """Uma execução do monitor — fast-scan de barreira + batch regular."""
    import uuid as _uuid_mod
    from ..database import CeleryAsyncSessionLocal

    run_id = str(_uuid_mod.uuid4())

    # ── Fast-scan: fecha lote limitado de barreiras rompidas antes do batch ──
    # O limite impede que gravações pós-fechamento monopolizem o worker; o beat
    # continua drenando o backlog nos ticks seguintes.
    fast_scan_result = await _fast_barrier_scan_async(run_id)

    summary: Dict[str, Any] = {
        "processed": 0,
        "completed": 0,
        "errors": 0,
        "backfill_created": 0,
        "watchlist_spot_created": 0,
        **fast_scan_result,
    }
    # Coletados dentro da tx principal e consumidos após o commit — FIX C3/D1.
    enrich_targets: List[Dict[str, Any]] = []
    # FIX D1 (2026-05-15): IDs dos shadows fechados neste tick — gravação de
    # simulação em sessão isolada APÓS o commit da tx principal.
    sim_targets: List[Any] = []

    async with CeleryAsyncSessionLocal() as db:
        async with db.begin():
            force_close_policy = await _load_shadow_force_close_policy(db)
            # Carrega batch determinístico (sorted by id) — gotcha #251/#273.
            # FOR UPDATE SKIP LOCKED garante que duas execuções
            # concorrentes do monitor (ad-hoc dispatch + beat tick
            # sobreposto, ou múltiplos workers da execution queue) NÃO
            # processem o mesmo shadow_trade no mesmo tick.
            res = await db.execute(
                select(ShadowTrade)
                .where(ShadowTrade.status.in_(("PENDING", "RUNNING")))
                .order_by(ShadowTrade.created_at.asc(), ShadowTrade.id.asc())
                .with_for_update(skip_locked=True)
                .limit(SHADOW_MONITOR_BATCH_SIZE)
            )
            shadows = list(res.scalars().all())
            # Re-sort defensivamente — ORM já devolve em ordem por
            # ORDER BY, mas sorted() reforça invariante deadlock-safety.
            shadows.sort(key=lambda s: (s.created_at, s.id))

            for shadow in shadows:
                summary["processed"] += 1
                try:
                    transition = await _advance_shadow(db, shadow, force_close_policy)
                    if transition == "completed":
                        summary["completed"] += 1
                        # FIX D1: coleta ID antes do commit para gravação
                        # de simulação em sessão isolada pós-commit.
                        sim_targets.append(shadow.id)
                except Exception:
                    summary["errors"] += 1
                    logger.exception(
                        "[shadow-monitor] advance failed for shadow_id=%s",
                        shadow.id,
                    )

            # Snapshot de dados escalares ANTES do commit — ORM objects
            # ficam detached após o fechamento da sessão. Filtro por
            # entry_timestamp não-NULL replica o guard de _enrich_market_context
            # (só enriquece shadows com entrada confirmada).
            enrich_targets = [
                {
                    "shadow_id": s.id,
                    "symbol": s.symbol,
                    "entry_timestamp": s.entry_timestamp,
                    "decision_id": s.decision_id,
                    "needs_fill": (
                        s.btc_price_at_entry is None
                        or s.btc_change_1h_pct is None
                        or s.funding_rate_at_entry is None
                        or s.n_concurrent_signals is None
                    ),
                }
                for s in shadows
                if s.entry_timestamp is not None
            ]
    # ── Pós-commit: operações best-effort em sessões isoladas ─────────────────
    # A tx principal já commitou todos os shadow.status='COMPLETED'. A partir
    # daqui, qualquer falha SQL não desfaz os fechamentos (FIX C3/D1).

    # Simulação ML best-effort — tx isolada por shadow (FIX D1, 2026-05-15).
    # record_as_simulation + _capture_exit_features usam db_sim próprio;
    # UndefinedColumnError ou lock em trade_simulations não aborta fechamentos.
    post_commit_deadline = _new_post_commit_deadline()
    _, deferred_sim = await _run_best_effort_budgeted(
        sim_targets,
        _record_simulation_one_async,
        deadline=post_commit_deadline,
        item_label="shadow_simulations",
    )

    # Enriquecimento ML best-effort — tx isolada por shadow (FIX C3, 2026-05-15).
    enrich_work = [t for t in enrich_targets if t["needs_fill"]]

    async def _run_enrich_target(target: Dict[str, Any]) -> None:
        await _enrich_one_async(
            target["shadow_id"],
            target["symbol"],
            target["entry_timestamp"],
            target["decision_id"],
        )

    _, deferred_enrich = await _run_best_effort_budgeted(
        enrich_work,
        _run_enrich_target,
        deadline=post_commit_deadline,
        item_label="shadow_enrichment",
    )
    summary["deferred_simulations"] = deferred_sim
    summary["deferred_enrichment"] = deferred_enrich

    # Reativa edge trigger L3_REJECTED no Redis para shadows que completaram.
    # Remove o símbolo de prior_rejected_visibility para que o próximo ciclo
    # do pipeline_scan crie um novo shadow imediatamente (sem esperar 24h TTL).
    # Backfill best-effort: cria shadows para símbolos aprovados na watchlist
    # sem shadow RUNNING. Cobre o gap entre pipeline_scan (apenas transições
    # de estado → decisions_log) e execute_buy (janela 10 min). Idempotente
    # via ON CONFLICT (decision_id) DO NOTHING.
    summary["backfill_created"] = await _backfill_shadows_for_all_users()

    # BLOCO B — WATCHLIST_SPOT: captura espectro completo da L1 spot.
    # Gated por new_arch_capture_enabled — quando False, no-op (zero impacto).
    # ADITIVO ao backfill L3 existente: não substitui, roda em paralelo.
    summary["watchlist_spot_created"] = await _create_watchlist_spot_shadows_for_all_users()

    # P0 backfill: label decisions_log rows whose pnl_pct is still NULL even
    # though the linked shadow trade has COMPLETED. Catches rows persisted
    # before the record_as_simulation writeback fix was deployed.
    # Capped at 500 rows/beat to avoid blocking on a large historical backlog.
    from ..services.shadow_trade_service import backfill_decisions_log_pnl_from_shadows
    summary["pnl_backfill"] = await backfill_decisions_log_pnl_from_shadows(limit=500)

    return summary




async def _backfill_shadows_for_all_users() -> int:
    """Carrega SpotEngineConfig por usuário e chama safe_backfill_watchlist_shadows."""
    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig
    from ..services.shadow_trade_service import safe_backfill_watchlist_shadows
    from sqlalchemy import select

    try:
        async with CeleryAsyncSessionLocal() as db:
            cfg_res = await db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                )
            )
            cfg_rows = cfg_res.scalars().all()

        total = 0
        for cfg_row in cfg_rows:
            try:
                se_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)
                user_config = {
                    "tp_pct": float(se_cfg.selling.take_profit_pct),
                    "sl_pct": float(
                        se_cfg.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    ),
                    "timeout_candles": None,
                }
                total += await safe_backfill_watchlist_shadows(cfg_row.user_id, user_config)
            except Exception:
                logger.exception(
                    "[shadow-monitor] backfill failed for user=%s", cfg_row.user_id
                )
        return total
    except Exception:
        logger.exception("[shadow-monitor] _backfill_shadows_for_all_users failed")
        return 0


async def _create_watchlist_spot_shadows_for_all_users() -> int:
    """BLOCO B — cria shadows WATCHLIST_SPOT para espectro completo da L1 spot.

    Gated por ``new_arch_capture_enabled`` em pool_config. Quando False,
    retorna 0 sem alterar nada — comportamento IDÊNTICO ao atual.

    Roda como best-effort em paralelo ao backfill L3 existente (ADITIVO).
    Nunca levanta exceção — falhas são logadas e não propagadas.
    """
    from ..database import CeleryAsyncSessionLocal
    from ..models.config_profile import ConfigProfile
    from ..schemas.spot_engine_config import SpotEngineConfig
    from ..services.shadow_trade_service import (
        create_watchlist_spot_shadows,
        TTT_ENABLED_DEFAULT,
        TTT_TP_PCT_DEFAULT,
        TTT_TIMEOUT_MINUTES_DEFAULT,
        _SHADOW_SL_PCT_OVERRIDE,
    )
    from sqlalchemy import select

    try:
        async with CeleryAsyncSessionLocal() as db:
            # Carrega spot_engine configs (TP/SL) + pool_config (flags + l1_id)
            se_res = await db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.config_type == "spot_engine",
                    ConfigProfile.is_active.is_(True),
                )
            )
            se_rows = se_res.scalars().all()

            pool_res = await db.execute(
                select(ConfigProfile).where(
                    ConfigProfile.config_type == "pool_config",
                    ConfigProfile.is_active.is_(True),
                )
            )
            pool_rows = {str(r.user_id): r.config_json for r in pool_res.scalars().all()}

        total = 0
        for cfg_row in se_rows:
            uid = str(cfg_row.user_id)
            pool_cfg = pool_rows.get(uid, {})
            if not pool_cfg.get("new_arch_capture_enabled", False):
                continue
            try:
                se_cfg = SpotEngineConfig.from_config_json(cfg_row.config_json)
                user_config = {
                    "tp_pct": float(se_cfg.selling.take_profit_pct),
                    "sl_pct": _SHADOW_SL_PCT_OVERRIDE or float(
                        se_cfg.sell_flow.kill_switch.max_drawdown_from_hwm_pct
                    ),
                    "timeout_candles": None,
                    "ttt_enabled": TTT_ENABLED_DEFAULT,
                    "ttt_tp_pct": TTT_TP_PCT_DEFAULT,
                    "ttt_timeout_minutes": TTT_TIMEOUT_MINUTES_DEFAULT,
                }
                total += await create_watchlist_spot_shadows(
                    cfg_row.user_id, user_config, pool_cfg
                )
            except Exception:
                logger.exception(
                    "[shadow-monitor] WATCHLIST_SPOT creation failed user=%s", uid
                )
        return total
    except Exception:
        logger.exception("[shadow-monitor] _create_watchlist_spot_shadows_for_all_users failed")
        return 0


@celery_app.task(name="app.tasks.shadow_trade_monitor.run", bind=True)
def run(self) -> str:
    """Beat-driven monitor — default a cada ``SHADOW_MONITOR_INTERVAL_S`` s."""
    try:
        result = _run_async(_monitor_async())
        backfill = result.get("backfill_created", 0)
        fast_tp = result.get("fast_scan_closed_tp", 0)
        fast_sl = result.get("fast_scan_closed_sl", 0)
        fast_stale = result.get("fast_scan_skipped_stale", 0)
        fast_errors = result.get("fast_scan_errors", 0)
        msg = (
            f"Shadow monitor: {result['processed']} processed, "
            f"{result['completed']} completed, {result['errors']} errors, "
            f"{backfill} backfill | "
            f"fast-scan closed_tp={fast_tp} closed_sl={fast_sl} "
            f"stale_skipped={fast_stale} errors={fast_errors}"
        )
        logger.info("[shadow-monitor] %s", msg)
        return msg
    except Exception as exc:
        logger.error("[shadow-monitor] task failed: %s", exc, exc_info=True)
        raise
